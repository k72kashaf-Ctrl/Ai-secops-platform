# Full locally verifiable acceptance command.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

Write-Host "== Compile check =="
python -m compileall -q app ui tests

Write-Host "== Functional tests =="
python -m pytest -q -m "not performance"
if ($LASTEXITCODE -ne 0) { throw "Functional tests failed." }

Write-Host "== Performance tests =="
python -m pytest -q -m performance -s
if ($LASTEXITCODE -ne 0) { throw "Performance test failed." }

Write-Host "== Required artifacts =="
$required = @(
    "README.md",
    "requirements.txt",
    "Dockerfile",
    "docker-compose.yml",
    "app\main.py",
    "app\api\assistant.py",
    "app\api\reasoning.py",
    "app\services\assistant.py",
    "app\services\reasoning.py",
    "data\sample_events.json",
    "scripts\generate_events.py",
    "tests",
    "..\docs\FUNCTIONAL_TEST_MATRIX.md"
)
foreach ($path in $required) {
    if (-not (Test-Path $path)) { throw "Missing required artifact: $path" }
}

Write-Host "VERIFY_ALL: PASS"
