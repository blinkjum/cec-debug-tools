"""Four task pages, shared dialogs and old/new script compatibility."""
import importlib.util
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
from unittest import TestCase, main
from unittest.mock import patch, PropertyMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cec_scripts import compile_script
from libcec_backend import BackendResult


class ScriptCompatibilityTests(TestCase):
    def test_old_and_new_waits_comments_and_bom_preserve_commands_and_line_numbers(self):
        old = compile_script(['\ufeff# old', '', ' tx 05:8F ', '@wait 12.5', 'pow 5'], legacy_interval_ms=17)
        self.assertEqual(old.dialect, 'LEGACY')
        self.assertEqual(old.interval_ms, 17)
        self.assertEqual([(s.line_number, s.kind, s.value) for s in old.steps],
                         [(3, 'command', 'tx 05:8F'), (4, '@wait', '12.5'), (5, 'command', 'pow 5')])
        new = compile_script(['tx 05:8F', 'wait 5', 'expect 50:90:00 timeout=100',
                              'expect-opcode 90 timeout=100', 'assert last-rx-opcode=90'])
        self.assertEqual(new.dialect, 'SCENARIO')
        self.assertEqual(new.interval_ms, 80)
        self.assertEqual([s.kind for s in new.steps], ['command', 'wait', 'expect', 'expect-opcode', 'assert'])

    def test_ambiguous_pure_commands_have_explicit_legacy_override(self):
        lines = ['scan', 'pow 5', 'name 5', 'ver 5']
        self.assertEqual(compile_script(lines).dialect, 'SCENARIO')
        self.assertEqual(compile_script(lines, path_hint='old.CMD', legacy_interval_ms=3).interval_ms, 3)
        self.assertEqual(compile_script(lines, 'LEGACY', 0.2).interval_ms, 0.2)
        self.assertEqual(compile_script(lines, 'SCENARIO', 'unused').interval_ms, 80)
        self.assertEqual(compile_script(['@wait 0', 'expect-opcode 90']).dialect, 'SCENARIO')

    def test_invalid_waits_and_pacing_fail_before_a_plan_is_returned(self):
        for lines in (['tx 05:8F', '@wait nope'], ['wait'], ['wait -1'], ['@wait inf'], ['@wait nan'], ['assert']):
            with self.subTest(lines=lines), self.assertRaises(ValueError):
                compile_script(lines)
        for interval in ('bad', '-1', float('inf')):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                compile_script(['pow 5'], 'LEGACY', interval)
        with self.assertRaises(ValueError):
            compile_script([], 'UNKNOWN')


class WorkspaceGuiTests(TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('workspace_gui', ROOT/'cec-client-gui.py')
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        spec.loader.exec_module(cls.module)

    def setUp(self):
        folder = ROOT/'work/ui-workspace-tests'
        folder.mkdir(parents=True, exist_ok=True)
        self.scratch = tempfile.TemporaryDirectory(dir=folder)
        self.gui = self.module.CECClientGUI(read_settings=False, start_hidden=True)
        self.gui.settings_path = Path(self.scratch.name)/'settings.json'
        self.gui.wb_annotations_path = Path(self.scratch.name)/'bookmarks.json'
        self.gui.log_file_var.set('')
        self.gui.short_log_file_var.set('')

    def tearDown(self):
        self.gui.update_idletasks()
        self.gui.log_writer.close()
        self.gui.destroy()
        self.scratch.cleanup()

    def drain(self):
        items = []
        while True:
            try:
                items.append(self.gui.output_queue.get_nowait())
            except queue.Empty:
                return items

    def test_exactly_four_pages_default_monitor_and_all_features_have_a_home(self):
        g = self.gui
        self.assertEqual([g.notebook.tab(page, 'text') for page in g.notebook.tabs()],
                         ['报文监视', '发包与模拟', '设备与拓扑', '测试与报告'])
        self.assertEqual(g.notebook.select(), str(g.capture_tab_widget))
        self.assertEqual([g.device_notebook.tab(p, 'text') for p in g.device_notebook.tabs()],
                         ['手动发包', '角色自动应答', '逻辑地址占用'])
        self.assertEqual(len(g.cts_cases), 102)
        self.assertTrue(g.topology_tree.winfo_exists())
        self.assertIs(g.script_text, g.scenario_text)
        self.assertEqual(g.diagnostics_window.state(), 'withdrawn')

    def test_quick_commands_share_raw_fields_and_load_does_not_transmit(self):
        g = self.gui
        for field in ('source', 'destination', 'opcode', 'params'):
            self.assertIs(g.wb_quick_vars[field], getattr(g, f'raw_{field}_var'))
        g.raw_source_var.set('0')
        g.raw_destination_var.set('5')
        g.raw_opcode_var.set('44')
        g.raw_params_var.set('41')
        g.wb_release_var.set(True)
        self.assertEqual(g._wb_quick_draft().frames(), ['05:44:41', '05:45'])
        self.assertEqual(g.raw_frame_var.get(), '05:44:41')
        with patch.object(g.backend, 'transmit') as send:
            g._wb_to_raw()
            send.assert_not_called()
        self.assertEqual(g.notebook.select(), str(g.device_tab_widget))
        self.assertEqual(g.device_notebook.select(), str(g.raw_tab_widget))

    def test_diagnostics_keep_the_same_output_and_hide_instead_of_destroy(self):
        g = self.gui
        g._append_output('before opening\n')
        text = g.output
        g._open_advanced_command()
        self.assertTrue(g.console_page.winfo_manager())
        self.assertEqual(g.notebook.select(), str(g.capture_tab_widget))
        g.diagnostics_window.withdraw()
        g._append_output('while hidden\n')
        g._workspace_show_diagnostics()
        self.assertFalse(g.console_page.winfo_manager())
        self.assertIs(g.output, text)
        self.assertIn('before opening\nwhile hidden', g.output.get('1.0', 'end'))
        with patch.object(g, '_show_stats') as stats:
            g._workspace_diagnostic_action(g._show_stats)
            stats.assert_called_once()

    def test_connection_settings_use_original_variables_and_topology_stays_in_device_page(self):
        g = self.gui
        g._workspace_show_connection_settings()
        window = g._connection_settings_window
        g._workspace_show_connection_settings()
        self.assertIs(window, g._connection_settings_window)
        g.port_var.set('COM9')
        g.types_var.set('a')
        g.osd_name_var.set('AVR test')
        g.vendor_id_var.set('080046')
        g._workspace_save_connection_settings()
        self.assertTrue(g.log_writer.flush(wait=True))
        saved = json.loads(g.settings_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['port'], 'COM9')
        self.assertEqual(saved['osd_name'], 'AVR test')
        self.assertIn('COM9', g.equipment_summary_var.get())
        self.assertIn('AVR test', g.equipment_summary_var.get())
        self.assertEqual(window.state(), 'withdrawn')
        self.assertEqual(g.topology_page.master, g.equipment_tab_widget)

    def test_one_console_action_sends_complete_command_through_existing_connection(self):
        g = self.gui
        g.console_command_var.set('tx 05:8F')
        with patch.object(type(g.backend), 'is_open', new_callable=PropertyMock, return_value=True), \
             patch.object(g.backend, 'execute_command', return_value=BackendResult(True, 'ACK')) as execute:
            g._send_selected()
            execute.assert_called_once_with('tx 05:8F')
        self.assertIn('ACK', g.output.get('1.0', 'end'))

    def test_legacy_worker_orders_commands_honors_wait_and_supports_stop(self):
        g = self.gui
        plan = compile_script(['scan', '@wait 0', 'pow 5', 'name 5'], 'LEGACY', 4)
        with patch.object(g.scenario_stop_event, 'wait', return_value=False) as wait:
            g._scenario_thread(plan)
        events = self.drain()
        self.assertEqual([value for kind, value in events if kind == 'scenario_command'], ['scan', 'pow 5', 'name 5'])
        self.assertEqual([call.args[0] for call in wait.call_args_list], [0.004, 0, 0.004, 0.004])
        self.assertTrue(any(kind == 'scenario_status' and '完成' in value for kind, value in events))
        g.scenario_stop_event.set()
        g._scenario_thread(compile_script(['pow 5'], 'LEGACY', 0))
        self.assertFalse(any(kind == 'scenario_command' for kind, _ in self.drain()))

    def test_scenario_expect_assert_and_at_wait_share_one_worker(self):
        g = self.gui
        plan = compile_script(['tx 05:8F', '@wait 0', 'expect 50:90:00 timeout=40',
                               'expect-opcode 90 timeout=50', 'assert last-rx-opcode=90'], 'SCENARIO')
        with patch.object(g.scenario_stop_event, 'wait', return_value=False), \
             patch.object(g, '_scenario_wait_for_match', return_value=(True, 1)) as expect, \
             patch.object(g, '_scenario_assert', return_value=True) as check:
            g._scenario_thread(plan)
        self.assertEqual(expect.call_args_list[0].kwargs, dict(frame='50:90:00', timeout_ms=40))
        self.assertEqual(expect.call_args_list[1].kwargs, dict(opcode=0x90, timeout_ms=50))
        check.assert_called_once_with('last-rx-opcode=90', 1)
        self.assertTrue(any(kind == 'scenario_status' and '3 项检查' in value for kind, value in self.drain()))

    def test_legacy_open_save_hooks_and_pacing_preferences_use_the_shared_editor(self):
        g = self.gui
        source = Path(self.scratch.name)/'old.cmd'
        content = '# 保留旧脚本\nscan\n@wait 1000\npow 5\n'
        source.write_text(content, encoding='utf-8-sig')
        with patch.object(self.module.filedialog, 'askopenfilename', return_value=str(source)):
            g._open_script()
        self.assertEqual(g.scenario_text.get('1.0', 'end-1c'), content)
        self.assertEqual(g.script_dialect_var.get(), 'LEGACY')
        g.script_delay_var.set('23')
        target = Path(self.scratch.name)/'saved.cec'
        with patch.object(self.module.filedialog, 'asksaveasfilename', return_value=str(target)):
            g._save_script()
        self.assertEqual(target.read_text(encoding='utf-8').strip(), content.strip())
        g._save_settings()
        self.assertTrue(g.log_writer.flush(wait=True))
        saved = json.loads(g.settings_path.read_text(encoding='utf-8'))
        self.assertEqual((saved['script_dialect'], saved['script_delay']), ('LEGACY', '23'))

    def test_legacy_run_wrapper_and_invalid_script_never_start_a_second_runner(self):
        g = self.gui
        with patch.object(g, '_run_scenario') as run:
            g._run_script()
            run.assert_called_once_with(dialect='LEGACY')
        g.scenario_text.delete('1.0', 'end')
        g.scenario_text.insert('1.0', 'tx 05:8F\n@wait bad')
        with patch.object(type(g.backend), 'is_open', new_callable=PropertyMock, return_value=True), \
             patch.object(self.module.threading, 'Thread') as thread, patch.object(self.module.messagebox, 'showwarning') as warning:
            g._run_scenario()
            thread.assert_not_called()
            warning.assert_called_once()

    def test_saved_custom_raw_frame_and_legacy_pacing_survive_startup(self):
        g = self.gui
        g.raw_source_var.set('5')
        g.raw_destination_var.set('0')
        g.raw_opcode_var.set('46')
        g.raw_params_var.set('')
        g.raw_frame_var.set('0F:FF:AA:BB')
        g.script_dialect_var.set('LEGACY')
        g.script_delay_var.set('23')
        g._save_settings()
        self.assertTrue(g.log_writer.flush(wait=True))
        with patch.object(self.module.CECClientGUI, '_settings_path', return_value=g.settings_path):
            restored = self.module.CECClientGUI(read_settings=True, start_hidden=True)
        try:
            self.assertEqual(restored.raw_frame_var.get(), '0F:FF:AA:BB')
            self.assertEqual((restored.raw_source_var.get(), restored.raw_destination_var.get(), restored.raw_opcode_var.get()), ('5', '0', '46'))
            self.assertEqual((restored.script_dialect_var.get(), restored.script_delay_var.get()), ('LEGACY', '23'))
            self.assertEqual(restored.notebook.select(), str(restored.capture_tab_widget))
            restored.raw_params_var.set('43')
            self.assertEqual(restored.raw_frame_var.get(), '50:46:43')
        finally:
            restored.log_writer.close()
            restored.destroy()


if __name__ == '__main__':
    main()
