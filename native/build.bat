@echo off
setlocal EnableDelayedExpansion
rem Kill leftover player processes first: deferred model unload keeps python.exe alive
rem after the window closes, and a running process locks sumu_core.pyd -> LNK1104 at link.
rem Match is limited to this repo's entrypoints so unrelated python processes are spared.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'play\.py|run_player\.py|sumu\.app|sumu_core' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

cd /d "%~dp0"
set "NATIVE_DIR=%cd%"
for %%I in ("%NATIVE_DIR%\..") do set "REPO_ROOT=%%~fI"

if not defined SUMU_PYTHON set "SUMU_PYTHON=%REPO_ROOT%\.venv\Scripts\python.exe"
if not exist "%SUMU_PYTHON%" (
  echo BUILD FAILED: python not found at %SUMU_PYTHON%
  echo Set SUMU_PYTHON to the venv interpreter, or create .venv first.
  exit /b 1
)
set "PYBIND11_DIR=%REPO_ROOT%\.venv\Lib\site-packages\pybind11\share\cmake\pybind11"
if not exist "%PYBIND11_DIR%\pybind11Config.cmake" (
  echo BUILD FAILED: pybind11 CMake config missing at %PYBIND11_DIR%
  echo Run "uv sync" in the repo root first.
  exit /b 1
)

set "VCVARS="
if exist "%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" (
  set "VCVARS=%ProgramFiles(x86)%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
) else if exist "%ProgramFiles%\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat" (
  set "VCVARS=%ProgramFiles%\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat"
) else if exist "%ProgramFiles%\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat" (
  set "VCVARS=%ProgramFiles%\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat"
) else if exist "%ProgramFiles%\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" (
  set "VCVARS=%ProgramFiles%\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
) else if exist "%ProgramFiles%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" (
  set "VCVARS=%ProgramFiles%\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
)
if not defined VCVARS (
  echo BUILD FAILED: vcvars64.bat not found. Install VS2022 BuildTools or VS2022 with the C++ workload.
  exit /b 1
)
call "%VCVARS%"
if errorlevel 1 exit /b 1

if defined CUDA_PATH if exist "%CUDA_PATH%\lib\x64\cuda.lib" (
  cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release ^
    -DPython_EXECUTABLE="%SUMU_PYTHON%" ^
    -Dpybind11_DIR="%PYBIND11_DIR%" ^
    -DCUDA_TOOLKIT_ROOT="%CUDA_PATH%"
) else (
  cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release ^
    -DPython_EXECUTABLE="%SUMU_PYTHON%" ^
    -Dpybind11_DIR="%PYBIND11_DIR%"
)
if errorlevel 1 exit /b 1
cmake --build build
if errorlevel 1 exit /b 1
echo BUILD_OK
