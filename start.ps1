# Start the Zenith API and dashboard together on Windows PowerShell.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:ZENITH_API_URL = "http://127.0.0.1:8000"
$env:PYTHONIOENCODING = "utf-8"

$api = Start-Process -FilePath "python" `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
    -PassThru -NoNewWindow

Write-Host "Zenith API:  http://localhost:8000"
Write-Host "API docs:    http://localhost:8000/docs"

$ui = Start-Process -FilePath "python" `
    -ArgumentList @("-m", "streamlit", "run", "ui/dashboard.py", "--server.port", "8501", "--server.headless", "true") `
    -PassThru -NoNewWindow

Write-Host "Dashboard:   http://localhost:8501"
Write-Host "Press Ctrl+C to stop both processes."

try {
    Wait-Process -Id $api.Id
} finally {
    foreach ($process in @($api, $ui)) {
        if ($null -ne $process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
