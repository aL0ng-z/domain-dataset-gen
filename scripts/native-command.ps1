# Shared native-process gate for Windows PowerShell 5.1 and PowerShell 7.
function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][string]$FailureMessage,
        [string]$WorkDir = (Get-Location).Path
    )

    # Resolve before relaxing error handling so an absent executable cannot
    # accidentally reuse a previous process's successful LASTEXITCODE.
    $command = Get-Command -Name $Exe -CommandType Application -ErrorAction Stop | Select-Object -First 1
    $previousErrorActionPreference = $ErrorActionPreference
    $nativePreference = Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue
    Push-Location -LiteralPath $WorkDir -ErrorAction Stop
    try {
        $ErrorActionPreference = "Continue"
        if ($nativePreference) { $PSNativeCommandUseErrorActionPreference = $false }
        # PS 5.1 represents redirected stderr as ErrorRecord. Stream it as text;
        # warnings remain visible and only the process exit code decides success.
        & $command.Source @Arguments 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) {
                Write-Host $_.Exception.Message
            } else {
                Write-Host $_
            }
        }
        $exitCode = $LASTEXITCODE
        if ($exitCode -ne 0) {
            throw "$FailureMessage (exit code $exitCode)"
        }
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
        if ($nativePreference) { $PSNativeCommandUseErrorActionPreference = $nativePreference.Value }
        Pop-Location
    }
}
