<#
.SYNOPSIS
  Test native exit-code handling without connecting to any infrastructure.
.EXAMPLE
  conda activate DatasetGen
  .\scripts\test-native-command.ps1
#>
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "native-command.ps1")

$originalLocation = (Get-Location).Path
$originalPreference = $ErrorActionPreference
$nativePython = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source

$output = Invoke-NativeChecked -Exe $nativePython -Arguments @(
    "-c", "import sys; print('warning-zero', file=sys.stderr); print('stdout-zero')"
) -FailureMessage "Zero exit must pass" -WorkDir $PSScriptRoot 6>&1 | Out-String
if ($output -notmatch "warning-zero" -or $output -notmatch "stdout-zero") {
    throw "Native stdout and stderr must both remain visible"
}
if ($ErrorActionPreference -ne $originalPreference -or (Get-Location).Path -ne $originalLocation) {
    throw "Successful invocation did not restore caller state"
}

$failedAsExpected = $false
try {
    Invoke-NativeChecked -Exe $nativePython -Arguments @(
        "-c", "import sys; print('warning-seven', file=sys.stderr); sys.exit(7)"
    ) -FailureMessage "Expected failure" -WorkDir $PSScriptRoot
} catch {
    if ($_.Exception.Message -notmatch "Expected failure \(exit code 7\)") { throw }
    $failedAsExpected = $true
}
if (-not $failedAsExpected) { throw "Nonzero native exit must fail" }
if ($ErrorActionPreference -ne $originalPreference -or (Get-Location).Path -ne $originalLocation) {
    throw "Failed invocation did not restore caller state"
}

$missingFailed = $false
try {
    Invoke-NativeChecked -Exe "datasetgen-nonexistent-native-command" -FailureMessage "Missing executable"
} catch {
    $missingFailed = $true
}
if (-not $missingFailed) { throw "Missing executable must fail" }

Write-Host "Native command regression tests passed (stderr/exit 0, exit 7, missing executable, caller state)."
