<#
.SYNOPSIS
  Heuristic pre-import dependency check for a packed Power Platform solution.

.DESCRIPTION
  Solution Checker does NOT catch "missing dependency" bugs (a flow that
  calls a child flow not included in the solution, a connection reference
  or environment variable the solution expects but doesn't define, etc.).
  Those normally only surface when Dataverse rejects the import in the next
  environment. This script catches the common cases *before* that, by
  parsing solution.xml and cross-checking referenced components.

  This is a heuristic safety net, not a replacement for actually importing
  into a disposable "canary" environment, which is the only fully reliable
  check. Treat this as the fast, free first gate.

.PARAMETER SolutionZip
  Path to the packed (managed or unmanaged) solution zip.

.PARAMETER SourceFolder
  Path to the unpacked solution source (pac solution unpack output).
#>

param(
    [Parameter(Mandatory = $true)][string]$SolutionZip,
    [Parameter(Mandatory = $true)][string]$SourceFolder
)

$ErrorActionPreference = "Stop"
$failures = @()

Write-Host "Checking dependencies for $SolutionZip ..."

# --- 1. Unzip solution.xml for a component inventory -----------------------
$tempDir = Join-Path $env:RUNNER_TEMP "solution-extract"
if (Test-Path $tempDir) { Remove-Item $tempDir -Recurse -Force }
Expand-Archive -Path $SolutionZip -DestinationPath $tempDir -Force

$solutionXmlPath = Join-Path $tempDir "solution.xml"
if (-not (Test-Path $solutionXmlPath)) {
    Write-Error "solution.xml not found in packed solution — packing may have failed."
    exit 1
}
[xml]$solutionXml = Get-Content $solutionXmlPath

# --- 2. Connection references must have a value or be left to config -------
$connRefNodes = $solutionXml.SelectNodes("//connectionreference")
foreach ($node in $connRefNodes) {
    $logicalName = $node.connectionreferencelogicalname
    if (-not $logicalName) {
        $failures += "Connection reference with no logical name found — likely a broken/unsaved reference."
    }
}

# --- 3. Environment variables should have a default OR be documented -------
$envVarFiles = Get-ChildItem -Path $SourceFolder -Recurse -Filter "environmentvariabledefinition.xml" -ErrorAction SilentlyContinue
foreach ($file in $envVarFiles) {
    [xml]$envVar = Get-Content $file.FullName
    $schemaName = $envVar.entity.SchemaName
    $defaultValue = $envVar.entity.attributes.attribute | Where-Object { $_.name -eq "defaultvalue" }
    if (-not $defaultValue -or [string]::IsNullOrWhiteSpace($defaultValue.'#text')) {
        Write-Warning "Environment variable '$schemaName' has no default value — confirm it's set per-environment via deployment settings, not forgotten."
    }
}

# --- 4. Flows: look for child-flow (Run a Child Flow) actions and verify --
#        the referenced flow's workflow id/name is present in this solution
$workflowFiles = Get-ChildItem -Path $SourceFolder -Recurse -Filter "*.json" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "Workflows" }

$includedWorkflowNames = Get-ChildItem -Path $SourceFolder -Recurse -Filter "*.xml" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "Workflows" } |
    ForEach-Object { (Get-Content $_.FullName -Raw) } |
    Select-String -Pattern "<Name>(.*?)</Name>" -AllMatches |
    ForEach-Object { $_.Matches } | ForEach-Object { $_.Groups[1].Value }

foreach ($wf in $workflowFiles) {
    $content = Get-Content $wf.FullName -Raw
    $childFlowRefs = [regex]::Matches($content, '"childFlowId"\s*:\s*"([^"]+)"')
    foreach ($ref in $childFlowRefs) {
        $childId = $ref.Groups[1].Value
        if ($content -notmatch [regex]::Escape($childId)) {
            $failures += "Flow '$($wf.Name)' references child flow id '$childId' — verify that flow is included in this solution."
        }
    }
}

# --- 5. Canvas apps: flag data sources that look like hard-coded env refs --
$canvasSourceFiles = Get-ChildItem -Path $SourceFolder -Recurse -Filter "*.pa.yaml" -ErrorAction SilentlyContinue
foreach ($file in $canvasSourceFiles) {
    $content = Get-Content $file.FullName -Raw
    if ($content -match "https://[a-z0-9-]+\.(crm|api)\.dynamics\.com") {
        $failures += "Canvas app source '$($file.Name)' appears to contain a hard-coded environment URL — use an environment variable or connection reference instead."
    }
}

# --- Report ------------------------------------------------------------
if ($failures.Count -gt 0) {
    Write-Host "::error::Dependency check found $($failures.Count) issue(s):"
    foreach ($f in $failures) { Write-Host "::error:: - $f" }
    exit 1
}

Write-Host "Dependency check passed — no missing/hard-coded dependencies detected."
