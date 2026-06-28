# read_usage.ps1 - Pitwall's real-usage reader (console-TEXT version, replaces OCR).
# Spawns `claude /usage` HIDDEN, then reads the rendered panel straight out of the
# console screen buffer as TEXT (Win32 ReadConsoleOutputCharacter) - the exact
# characters the TUI drew, so there is NO OCR and no misread digits. Prints ONE
# line of JSON to stdout:
#   {"ok":true,"session_pct":17,"session_reset":"4:10pm","weekall_pct":75,
#    "weekall_reset":"Jun 10, 5am","sonnet_pct":1,"raw":"<panel text>"}
# or {"ok":false,"error":"..."} on failure. All diagnostics go to stderr.
#
# Runs under either PowerShell, but invoked via WinPS 5.1 for parity. $0 token cost.
# SAFETY: only ever kills the claude.exe process tree WE spawned (seeded from our pid),
#         never a sibling session a user may open during the ~10s capture. (Ivan HIGH)
[CmdletBinding()]
param(
  [string]$ClaudeExe = "$env:USERPROFILE\.local\bin\claude.exe",
  # The folder claude launches in MUST be one Claude Code already trusts, or the spawned
  # session stalls on the "Do you trust this folder?" prompt and renders nothing. Empty
  # => auto-pick a trusted+existing folder from ~/.claude.json (the /usage panel is
  # account-level, so which trusted folder doesn't change the numbers).
  [string]$WorkDir   = "",
  [int]$MaxWaitSec   = 18,      # give the live /usage call time to render
  [switch]$RawOnly,            # print just the captured panel text (no JSON) - for the troubleshooting view
  [switch]$Trace,             # emit per-step timing (hop + cumulative ms) to stderr
  [switch]$DefineOnly        # define functions then return WITHOUT spawning - for tests only
)
$ErrorActionPreference = 'Stop'
function Fail([string]$m){ [Console]::Out.WriteLine((@{ok=$false;error=$m}|ConvertTo-Json -Compress)); exit 0 }
trap { Fail ("unhandled: " + $_.Exception.Message) }

# --- step timer (traceroute-style: each hop's own ms + cumulative) ---
$script:sw = [System.Diagnostics.Stopwatch]::StartNew()
$script:tprev = 0.0
function Trace([string]$label){
  if(-not $Trace){ return }
  $now = $script:sw.Elapsed.TotalMilliseconds
  $hop = $now - $script:tprev
  $script:tprev = $now
  [Console]::Error.WriteLine(("[trace] hop {0,7:N0}ms | total {1,7:N0}ms | {2}" -f $hop, $now, $label))
}

# --- find a trusted folder so /usage actually renders (see capture_usage.ps1 for the why) ---
function Resolve-TrustedWorkDir {
  $fallback = $env:USERPROFILE
  try {
    $cj = Join-Path $env:USERPROFILE '.claude.json'
    if(-not (Test-Path $cj)){ return $fallback }
    $key = $null
    foreach($ln in (Get-Content $cj)){
      $mk = [regex]::Match($ln, '^ {4}"(.+)":\s*\{\s*$')
      if($mk.Success){ $key = $mk.Groups[1].Value; continue }
      if($key -and $ln -match '^\s*"hasTrustDialogAccepted"\s*:\s*true'){
        $p = $key -replace '\\\\','\'
        if(Test-Path -LiteralPath $p){ return $p }
        $key = $null
      }
    }
  } catch {}
  return $fallback
}
if(-not $WorkDir){ $WorkDir = Resolve-TrustedWorkDir }
Trace "resolved trusted workdir"

Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class PitwallCon {
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool AttachConsole(uint pid);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool FreeConsole();
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] public static extern IntPtr CreateFileW(
    string name, uint access, uint share, IntPtr sec, uint disp, uint flags, IntPtr tmpl);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool GetConsoleScreenBufferInfo(IntPtr h, out CSBI info);
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] public static extern bool ReadConsoleOutputCharacterW(
    IntPtr h, StringBuilder buf, uint len, uint coord, out uint read);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool WriteConsoleInputW(
    IntPtr h, INPUT_RECORD[] buf, uint len, out uint written);
  [DllImport("kernel32.dll", SetLastError=true)] public static extern bool CloseHandle(IntPtr h);
  [StructLayout(LayoutKind.Sequential)] public struct COORD { public short X, Y; }
  [StructLayout(LayoutKind.Sequential)] public struct SMALL_RECT { public short L, T, R, B; }
  [StructLayout(LayoutKind.Sequential)] public struct CSBI {
    public COORD Size; public COORD Cursor; public ushort Attr; public SMALL_RECT Win; public COORD MaxWin; }
  [StructLayout(LayoutKind.Sequential)] public struct KEY_EVENT_RECORD {
    public int bKeyDown; public ushort wRepeatCount; public ushort wVirtualKeyCode;
    public ushort wVirtualScanCode; public ushort UnicodeChar; public uint dwControlKeyState; }
  [StructLayout(LayoutKind.Explicit)] public struct INPUT_RECORD {
    [FieldOffset(0)] public ushort EventType; [FieldOffset(4)] public KEY_EVENT_RECORD KeyEvent; }
}
"@
$GENERIC_RW = [uint32]3221225472   # 0xC0000000 GENERIC_READ|GENERIC_WRITE
$SHARE_RW   = [uint32]3            # FILE_SHARE_READ|WRITE
$OPEN_EXIST = [uint32]3            # OPEN_EXISTING

function Read-ConsoleText([int]$targetPid) {
  [PitwallCon]::FreeConsole() | Out-Null
  if (-not [PitwallCon]::AttachConsole([uint32]$targetPid)) { return $null }
  try {
    $h = [PitwallCon]::CreateFileW("CONOUT$", $GENERIC_RW, $SHARE_RW, [IntPtr]::Zero, $OPEN_EXIST, 0, [IntPtr]::Zero)
    if ($h -eq [IntPtr]-1 -or $h -eq [IntPtr]::Zero) { return $null }
    try {   # close the CONOUT$ handle even if a read throws (Ivan LOW-2)
      $info = New-Object PitwallCon+CSBI
      if (-not [PitwallCon]::GetConsoleScreenBufferInfo($h, [ref]$info)) { return $null }
      $w = $info.Size.X
      $rows = New-Object System.Collections.Generic.List[string]
      for ($y = $info.Win.T; $y -le $info.Win.B; $y++) {
        $sb = New-Object System.Text.StringBuilder ($w + 1)
        $read = 0
        [PitwallCon]::ReadConsoleOutputCharacterW($h, $sb, [uint32]$w, ([uint32]$y -shl 16), [ref]$read) | Out-Null
        $rows.Add($sb.ToString().TrimEnd())
      }
      return ($rows -join "`n")
    } finally {
      [PitwallCon]::CloseHandle($h) | Out-Null
    }
  } finally {
    [PitwallCon]::FreeConsole() | Out-Null
    [PitwallCon]::AttachConsole([uint32]4294967295) | Out-Null   # ATTACH_PARENT_PROCESS
  }
}

# Send a single ESCAPE keypress to a spawned session's console input. Used ONLY to clear
# a first-run interstitial (e.g. the "Claude in Chrome extension detected" prompt, 2026-06)
# that draws BEFORE the /usage panel and blocks it forever in a hidden, no-stdin spawn.
# Esc is the safe choice: on these prompts it's the explicit "decline / keep off / back"
# action (never "confirm"), so a mis-timed Esc can't enable anything - worst case it's a
# no-op. Only ever targets a pid in OUR spawned tree. Returns $true if the keys were posted.
function Send-ConsoleEsc([int]$targetPid) {
  # Pid-recycle guard (workspace [RULE], learned 2026-06-11): a tree pid could die and be
  # recycled into an unrelated process between enumeration and this write, sending it a
  # stray Esc. Re-verify the image is a Claude process IMMEDIATELY before attaching - the
  # /usage TUI is claude.exe (node child as a fallback). Exact-name match, never substring.
  $img = (Get-Process -Id $targetPid -ErrorAction SilentlyContinue).ProcessName
  if ($img -ne 'claude' -and $img -ne 'node') { return $false }
  [PitwallCon]::FreeConsole() | Out-Null
  if (-not [PitwallCon]::AttachConsole([uint32]$targetPid)) { return $false }
  try {
    $h = [PitwallCon]::CreateFileW("CONIN$", $GENERIC_RW, $SHARE_RW, [IntPtr]::Zero, $OPEN_EXIST, 0, [IntPtr]::Zero)
    if ($h -eq [IntPtr]-1 -or $h -eq [IntPtr]::Zero) { return $false }
    try {
      $ke = New-Object PitwallCon+KEY_EVENT_RECORD
      $ke.wRepeatCount = 1; $ke.wVirtualKeyCode = 0x1B; $ke.wVirtualScanCode = 1   # VK_ESCAPE
      $ke.UnicodeChar = 0; $ke.dwControlKeyState = 0
      $ke.bKeyDown = 1
      $down = New-Object PitwallCon+INPUT_RECORD; $down.EventType = 1; $down.KeyEvent = $ke   # KEY_EVENT
      $keUp = $ke; $keUp.bKeyDown = 0                                                          # value-type copy
      $up = New-Object PitwallCon+INPUT_RECORD; $up.EventType = 1; $up.KeyEvent = $keUp
      $recs = [PitwallCon+INPUT_RECORD[]]@($down, $up)
      $written = 0
      return [PitwallCon]::WriteConsoleInputW($h, $recs, [uint32]2, [ref]$written)
    } finally { [PitwallCon]::CloseHandle($h) | Out-Null }
  } finally {
    [PitwallCon]::FreeConsole() | Out-Null
    [PitwallCon]::AttachConsole([uint32]4294967295) | Out-Null
  }
}

# Pure tree-builder (testable; no WMI call inside). Returns a hashtable {pid -> CreationDate}
# of $root plus its GENUINE descendants. Two guards defeat Windows pid-recycling, the cause
# of the 2026-06-27 "sync killed all CLIs" bug: Windows does NOT rewrite an orphaned
# process's recorded ParentProcessId when its parent exits, so a fresh spawn handed a
# recycled pid would otherwise adopt unrelated live sessions as its own children:
#   * created-after-spawn: a descendant must be created at/after $since (our spawn instant)
#     — excludes any PRE-EXISTING orphan (the observed bug, a depth-1 stale-parent match).
#   * monotonic edge: a child must be created at/after the CURRENT occupant of its parent
#     pid. A real child is always younger than its parent; an impostor whose parent edge is
#     stale is OLDER than that pid's current occupant, so it's rejected — this also closes
#     the depth>=2 within-window recycle. (Ivan HIGH safety invariant, MEDIUM-1/2.)
# Neither guard can drop a real child (a genuine descendant is always created after both the
# spawn and its own parent), so it only ever shrinks the kill set toward exactly-our-tree.
function Build-Tree($all, [int]$root, [datetime]$since) {
  if ($since -isnot [datetime]) { throw "Build-Tree: `$since must be [datetime]" }
  $own = @{}                                       # pid -> CreationDate we adopted it with
  foreach ($p in $all) {
    if ([int]$p.ProcessId -eq $root -and $p.CreationDate -is [datetime]) { $own[$root] = $p.CreationDate } }
  if (-not $own.ContainsKey($root)) { $own[$root] = $since }   # root already gone: anchor at spawn
  for ($i=0; $i -lt 8; $i++) {
    $add = $false
    foreach ($p in $all) {
      $cpid = [int]$p.ProcessId; $ppid = [int]$p.ParentProcessId
      if ($own.ContainsKey($ppid) -and -not $own.ContainsKey($cpid) -and $p.CreationDate -is [datetime] `
          -and $p.CreationDate -ge $since -and $p.CreationDate -ge $own[$ppid]) {
        $own[$cpid] = $p.CreationDate; $add = $true } }
    if (-not $add) { break }
  }
  $own
}
function Get-Tree([int]$root) {
  $all = Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, CreationDate
  Build-Tree $all $root $script:spawnStart
}

# Tests dot-source this file to exercise Build-Tree without spawning anything.
if ($DefineOnly) { return }

if(-not (Test-Path $ClaudeExe)){ Fail "claude.exe not found at $ClaudeExe" }
# Stamp the instant BEFORE the spawn: every genuine descendant is created after this, so
# it's the cutoff that fences out recycled-pid impostors in both Build-Tree and Kill-Ours.
$script:spawnStart = Get-Date
$proc = Start-Process -FilePath $ClaudeExe -ArgumentList '/usage' -WorkingDirectory $WorkDir -WindowStyle Hidden -PassThru
$ourTree = @{}                                  # pid -> CreationDate we adopted it with
Trace "spawned claude /usage (pid $($proc.Id))"

function Merge-Tree($h) { foreach($k in $h.Keys){ $ourTree[$k] = $h[$k] } }

function Kill-Ours {
  try { Merge-Tree (Get-Tree ([int]$proc.Id)) } catch {}
  foreach($k in @($ourTree.Keys)){
    try {
      # Kill-site identity = (pid, CreationDate). A pid we adopted could have died and been
      # recycled to an UNRELATED process since we saw it; kill ONLY if the live occupant's
      # CreationDate still EQUALS what we adopted it with. A recycle yields a new process =>
      # different creation time => skipped; a genuine survivor matches exactly. CreationDate
      # is a fixed per-process property, so the same process always compares equal across
      # reads. This closes the kill-site recycle race even if a stale pid is in the set.
      # (Ivan HIGH safety invariant, MEDIUM-1.)
      $lp = Get-CimInstance Win32_Process -Filter "ProcessId=$k" -ErrorAction SilentlyContinue
      if ($lp -and $lp.CreationDate -is [datetime] -and $lp.CreationDate -eq $ourTree[$k]) {
        Stop-Process -Id $k -Force -ErrorAction Stop
      }
    } catch {}
  }
}

# poll the console buffer until the panel shows "% used" (the live call has rendered).
# No fixed blind wait — we read from the start, so a fast render finishes early.
$text = $null
$firstReadable = $false
$escSent = 0                           # how many interstitial-dismiss Escs we've posted
$deadline = (Get-Date).AddSeconds($MaxWaitSec)
Start-Sleep -Milliseconds 500          # the console host needs a beat to exist at all
while ((Get-Date) -lt $deadline) {
  $tree = Get-Tree ([int]$proc.Id); Merge-Tree $tree
  foreach ($tp in $tree.Keys) {
    $t = Read-ConsoleText $tp
    if ($t) {
      if (-not $firstReadable) { $firstReadable = $true; Trace "console first readable" }
      # require BOTH a percent AND a Resets line: the panel draws fast enough that a
      # 500ms poll could otherwise catch a frame with the % but not yet the reset row.
      if ($t -match '%\s*used' -and $t -match 'Resets') { $text = $t; break }
      # Otherwise: if the "Claude in Chrome extension detected" first-run prompt (new
      # 2026-06) is sitting in FRONT of /usage, it blocks the panel forever in a hidden,
      # no-stdin spawn. Esc past it ("Esc to keep browser tools off" is the prompt's own
      # decline action) so the panel can draw. Match its EXACT wording only - a broad
      # "any prompt" match would also hit the usage panel's own mid-render frame (its tab
      # bar / "Esc to" footer) and Esc would navigate OUT of the panel. Capped so a
      # never-clearing screen can't spin. Esc-only - see Send-ConsoleEsc for why it's safe.
      elseif ($escSent -lt 5 -and ($t -match 'Chrome extension detected' -or $t -match 'keep browser tools off')) {
        if (Send-ConsoleEsc $tp) { $escSent++; Trace "sent Esc to clear Chrome interstitial ($escSent)" }
      }
    }
  }
  if ($text) { break }
  Start-Sleep -Milliseconds 500
}
Trace "panel rendered (% used seen)"
Kill-Ours
Trace "killed our process tree"
if (-not $text) { Fail "panel did not render (no '% used' text read)" }

if ($RawOnly) { [Console]::Out.WriteLine($text); exit 0 }

# --- parse the clean text: per section, the first "NN% used" and "Resets <time> (tz)" ---
$session_pct=$null; $session_reset=$null; $weekall_pct=$null; $weekall_reset=$null; $sonnet_pct=$null
$sec=''
foreach ($ln in ($text -split "`n")) {
  if     ($ln -match 'Current session')        { $sec='s'; continue }
  elseif ($ln -match 'all models')             { $sec='w'; continue }
  elseif ($ln -match 'Sonnet only')            { $sec='o'; continue }
  elseif ($ln -match "contributing|Last 24h")  { $sec=''  }
  if (-not $sec) { continue }
  if ($ln -match '(\d{1,3})\s*%\s*used') {
    $v=[int]$matches[1]
    if ($v -ge 0 -and $v -le 100) {
      if     ($sec -eq 's' -and $null -eq $session_pct) { $session_pct=$v }
      elseif ($sec -eq 'w' -and $null -eq $weekall_pct) { $weekall_pct=$v }
      elseif ($sec -eq 'o' -and $null -eq $sonnet_pct)  { $sonnet_pct=$v }
    }
  }
  if ($ln -match 'Resets\s+(.+?)\s*\(') {
    $rs=$matches[1].Trim()
    if     ($sec -eq 's' -and -not $session_reset) { $session_reset=$rs }
    elseif ($sec -eq 'w' -and -not $weekall_reset) { $weekall_reset=$rs }
  }
}

if ($null -eq $session_pct -and $null -eq $weekall_pct -and $null -eq $sonnet_pct) {
  Fail "no percentages parsed from panel"
}
# Reduce the raw panel to ASCII before it goes in the JSON: the bar/box glyphs and the
# stray console control chars carry no info (the numbers + reset text are all ASCII), and
# keeping them makes the JSON line fragile across the PowerShell->Python decode. Block
# elements -> '#' so the bars still read as bars; everything else non-printable -> space.
$rawAscii = -join ([char[]]$text | ForEach-Object {
  $code = [int][char]$_
  if     ($_ -eq "`n")                  { "`n" }
  elseif ($code -ge 32 -and $code -le 126) { [string]$_ }
  elseif ($code -ge 0x2580 -and $code -le 0x259F) { '#' }
  else   { ' ' }
})
Trace "parsed numbers (session=$session_pct weekall=$weekall_pct sonnet=$sonnet_pct)"
$out = [ordered]@{
  ok=$true; session_pct=$session_pct; session_reset=$session_reset;
  weekall_pct=$weekall_pct; weekall_reset=$weekall_reset; sonnet_pct=$sonnet_pct; raw=$rawAscii
}
[Console]::Out.WriteLine(($out | ConvertTo-Json -Compress))
Trace "emitted JSON (DONE)"
