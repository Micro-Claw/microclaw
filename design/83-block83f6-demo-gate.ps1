param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('fixtures','prepare','session','verify','cleanup','selftest')]
    [string]$Phase,
    [string]$Out
)
$ErrorActionPreference = 'Stop'
# Reuse 83f-3's active-slot interpreter, evidence pointer and feature checks.
& (Join-Path $PSScriptRoot '83-block83f3-demo-gate.ps1') -Phase $Phase -Out $Out -Block '83f6'
exit $LASTEXITCODE
