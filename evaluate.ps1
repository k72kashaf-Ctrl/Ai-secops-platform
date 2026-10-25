# Run the full acceptance suite on Windows and print a pass/fail summary.
#
#   .\evaluate.ps1            # functional tests + 50k performance test
#   .\evaluate.ps1 -Quick     # functional tests only
param(
    [switch]$Quick
)

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

Write-Host "== Functional test suite =="
python -m pytest -q -m "not performance"
$functionalStatus = $LASTEXITCODE

$performanceStatus = 0
$performanceLabel = "SKIPPED (-Quick)"

if (-not $Quick) {
    Write-Host ""
    Write-Host "== 50,000-event performance test =="
    python -m pytest -q -m performance -s
    $performanceStatus = $LASTEXITCODE
    $performanceLabel = if ($performanceStatus -eq 0) { "PASS" } else { "FAIL" }
}

$functionalLabel = if ($functionalStatus -eq 0) { "PASS" } else { "FAIL" }

Write-Host ""
Write-Host "================ ZENITH EVALUATION SUMMARY ================"
Write-Host "Functional tests : $functionalLabel (exit $functionalStatus)"
Write-Host "Performance test : $performanceLabel"
Write-Host "==========================================================="

if ($functionalStatus -eq 0 -and $performanceStatus -eq 0) {
    Write-Host "RESULT: PASS"
    exit 0
}

Write-Host "RESULT: FAIL"
exit 1
