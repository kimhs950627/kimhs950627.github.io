# OneNote 데스크톱(COM)에서 계층·수정시각을 뽑고, 지정한 페이지만 HTML 로 내보낸다.
# MCP(Graph) 레이트리밋을 피하려고 로컬 앱을 쓴다.
#
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File onenote-export.ps1 -Mode manifest -Out C:\...\manifest.json
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File onenote-export.ps1 -Mode export -PageId <id> -Out <dir> -Format html
#
param(
  [Parameter(Mandatory = $true)][ValidateSet('manifest', 'export', 'exportall')][string]$Mode,
  [string]$PageId = '',
  [string]$Out = '',
  [string]$Notebook = '',
  [int]$Limit = 0
)

$ErrorActionPreference = 'Stop'
$on = New-Object -ComObject OneNote.Application
$xmlNs = 'http://schemas.microsoft.com/office/onenote/2013/onenote'

function Get-HierarchyXml([int]$scope) {
  $xml = ''
  $on.GetHierarchy('', $scope, [ref]$xml)
  return [xml]$xml
}

if ($Mode -eq 'manifest') {
  # hsPages = 4 (notebooks > sections > pages)
  $doc = Get-HierarchyXml 4
  $nsm = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
  $nsm.AddNamespace('one', $xmlNs)
  $rows = @()
  foreach ($page in $doc.SelectNodes('//one:Notebook/one:Section/one:Page', $nsm)) {
    $sec = $page.ParentNode
    $nb = $sec.ParentNode
    $rows += [pscustomobject]@{
      notebook      = $nb.name
      section       = $sec.name
      page          = $page.name
      id            = $page.ID
      modified      = $page.lastModifiedTime
      pagelevel     = $page.pageLevel
      sectionid     = $sec.ID
      sectionpath   = $sec.path
    }
  }
  if ($Notebook -ne '') { $rows = $rows | Where-Object { $_.notebook -eq $Notebook } }
  $rows = $rows | Sort-Object modified -Descending
  if ($Limit -gt 0) { $rows = $rows | Select-Object -First $Limit }
  $json = $rows | ConvertTo-Json -Depth 5
  if ($Out -ne '') {
    $dir = Split-Path -Parent $Out
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    [System.IO.File]::WriteAllText($Out, $json, [System.Text.UTF8Encoding]::new($false))
    Write-Output ("ROWS=" + $rows.Count + " OUT=" + $Out)
  } else {
    Write-Output $json
  }
  exit 0
}

if ($Mode -eq 'exportall') {
  if ($Out -eq '') { throw 'Out directory is required' }
  if (-not (Test-Path $Out)) { New-Item -ItemType Directory -Path $Out -Force | Out-Null }
  $doc = Get-HierarchyXml 4
  $nsm = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
  $nsm.AddNamespace('one', $xmlNs)
  $done = 0; $failed = 0
  foreach ($page in $doc.SelectNodes('//one:Notebook/one:Section/one:Page', $nsm)) {
    if ($Notebook -ne '' -and $page.ParentNode.ParentNode.name -ne $Notebook) { continue }
    $safe = ($page.ID -replace '[^A-Za-z0-9!._-]', '_')
    $target = Join-Path $Out ($safe + '.xml')
    if (Test-Path $target) { $done++; continue }
    try {
      # Publish 는 섹션이 닫혀 있으면 실패(0x8004201A)하므로 페이지 XML 을 직접 받는다.
      $xml = ''
      $on.GetPageContent($page.ID, [ref]$xml, 0)
      [System.IO.File]::WriteAllText($target, $xml, [System.Text.UTF8Encoding]::new($false))
      $done++
    } catch {
      $failed++
      Write-Output ('FAIL ' + $page.name + ' :: ' + $_.Exception.Message)
    }
  }
  Write-Output ("EXPORTED=" + $done + " FAILED=" + $failed)
  exit 0
}

if ($Mode -eq 'export') {
  if ($PageId -eq '' -or $Out -eq '') { throw 'PageId and Out are required for export' }
  $dir = Split-Path -Parent $Out
  if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  # pfHTML = 7, pfMHTML = 2
  $fmt = 7
  $on.Publish($PageId, $Out, $fmt, '')
  Write-Output ("EXPORTED " + $Out)
  exit 0
}

throw 'unknown mode'
