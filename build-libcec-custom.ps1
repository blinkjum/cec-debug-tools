param(
    [string]$ToolsRoot = "$PSScriptRoot/work/build-tools",
    [string]$InstallPrefix = "$PSScriptRoot/libcec-custom-install"
)
$ErrorActionPreference = 'Stop'
$cmake = Join-Path $ToolsRoot 'cmake/data/bin/cmake.exe'
$ninja = Join-Path $ToolsRoot 'bin/ninja.exe'
$toolchain = Join-Path $ToolsRoot 'llvm-mingw-20261006-ucrt-x86_64/bin'
$compiler = Join-Path $toolchain 'x86_64-w64-mingw32-clang++.exe'
$cCompiler = Join-Path $toolchain 'x86_64-w64-mingw32-clang.exe'
foreach ($path in @($cmake, $ninja, $compiler, $cCompiler)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing portable build tool: $path (see README)" }
}
$buildDir = Join-Path $PSScriptRoot 'libcec-upstream/build-occupancy-x64'
$stageDir = Join-Path $PSScriptRoot 'work/occupancy-install'
$oldTaskPath = $env:PATH
try {
    $env:PATH = "$toolchain;$(Split-Path $ninja);$env:PATH"
    & $cmake -S "$PSScriptRoot/libcec-upstream" -B $buildDir -G Ninja `
        "-DCMAKE_MAKE_PROGRAM=$ninja" "-DCMAKE_C_COMPILER=$cCompiler" "-DCMAKE_CXX_COMPILER=$compiler" `
        '-DCMAKE_BUILD_TYPE=Release' '-DCMAKE_SHARED_LINKER_FLAGS=-static' `
        '-DDISABLE_CLIENT=ON' '-DDISABLE_STATIC=ON' '-DSKIP_PYTHON_WRAPPER=1' '-DENABLE_TEST_ACK_MASK_TESTS=ON' `
        "-DCMAKE_INSTALL_PREFIX=$stageDir"
    if ($LASTEXITCODE -ne 0) { throw 'libCEC configure failed' }
    & $cmake --build $buildDir --parallel 4
    if ($LASTEXITCODE -ne 0) { throw 'libCEC build failed' }
    & (Join-Path $buildDir 'src/libcec/cec-test-ack-mask.exe')
    if ($LASTEXITCODE -ne 0) { throw 'Native ACK-mask regression check failed; DLL will not be installed' }
    # A deadlock is a failure too: bound the native thread regression in a
    # separate process before installing a newly built library.
    $threadTest = Start-Process -FilePath (Join-Path $buildDir 'src/libcec/cec-test-threads.exe') -PassThru -WindowStyle Hidden
    if (-not $threadTest.WaitForExit(30000)) {
        $threadTest.Kill()
        $threadTest.WaitForExit()
        throw 'Native thread regression timed out; DLL will not be installed'
    }
    if ($threadTest.ExitCode -ne 0) { throw 'Native thread regression failed; DLL will not be installed' }
    & $cmake --install $buildDir
    if ($LASTEXITCODE -ne 0) { throw 'libCEC stage install failed' }
    $destination = Join-Path $InstallPrefix 'bin'
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    # Install alongside the running GUI's DLL. Windows locks loaded libraries;
    # a side-by-side file preserves both its capture session and the old build.
    $builtDll = Join-Path $stageDir 'bin/cec.dll'
    $digest = (Get-FileHash -LiteralPath $builtDll -Algorithm SHA256).Hash.ToLower()
    $dll = Join-Path $destination ("cec-occupancy-{0}.dll" -f $digest.Substring(0, 12))
    if (-not (Test-Path -LiteralPath $dll)) {
        Copy-Item -LiteralPath $builtDll -Destination $dll
    } elseif ((Get-FileHash -LiteralPath $dll -Algorithm SHA256).Hash.ToLower() -ne $digest) {
        throw "Existing versioned DLL has a mismatched hash: $dll"
    }
    Write-Output "Built native rawFrame + test ACK mask DLL: $dll"
    Write-Output 'Restart the GUI to load the new DLL.'
} finally {
    $env:PATH = $oldTaskPath
}
