# capture_usage.ps1 - Pitwall's real-usage reader.
# Spawns `claude /usage` OFF-SCREEN (never visible, never steals focus), captures the
# rendered panel via PrintWindow, OCRs it, and prints ONE line of JSON to stdout:
#   {"ok":true,"session_pct":41,"session_reset":"11:10am","weekall_pct":71,
#    "weekall_reset":"Jun 10, 5am","sonnet_pct":1}
# or {"ok":false,"error":"..."} on failure. All diagnostics go to stderr.
#
# MUST run under Windows PowerShell 5.1 (WinRT OCR projection). $0 token cost.
# SAFETY: only ever kills the claude.exe process tree WE spawned (seeded from our own
# spawned pid, descendants walked) — never any other session's claude processes.
[CmdletBinding()]
param(
  [string]$ClaudeExe = "$env:USERPROFILE\.local\bin\claude.exe",
  # The folder claude is launched in. It MUST be one Claude Code already trusts: an
  # untrusted/never-seen folder makes claude stop on the "Do you trust this folder?"
  # workspace-trust prompt instead of rendering /usage, so the capture reads nothing.
  # Empty (the default) => auto-pick a trusted+existing folder from ~/.claude.json; the
  # /usage panel is account-level so WHICH trusted folder doesn't affect the numbers.
  [string]$WorkDir   = "",
  [int]$RenderWaitSec = 11,
  [switch]$SaveDebug          # also save the capture to %TEMP%\pitwall_usage_capture.png
)
$ErrorActionPreference = 'Stop'
function Fail([string]$m){ [Console]::Out.WriteLine((@{ok=$false;error=$m}|ConvertTo-Json -Compress)); exit 0 }
trap { Fail ("unhandled: " + $_.Exception.Message) }

# Find a folder Claude Code already trusts (hasTrustDialogAccepted=true) so the spawned
# `claude /usage` renders instead of stalling on the trust prompt. ~/.claude.json is
# pretty-printed but can't be ConvertFrom-Json'd (it has duplicate case-only keys like
# c:/ReelSheet vs C:/ReelSheet, which both PS 5.1 and 7 reject), so we line-scan it:
# project keys sit at 4-space indent ("<path>": {) and each block's hasTrustDialogAccepted
# is a direct child. Return the first trusted folder that still exists; else USERPROFILE.
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
        $p = $key -replace '\\\\','\'           # un-escape JSON backslashes
        if(Test-Path -LiteralPath $p){ return $p }
        $key = $null                            # trusted but gone — keep looking
      }
    }
  } catch {}
  return $fallback
}
if(-not $WorkDir){ $WorkDir = Resolve-TrustedWorkDir }

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Runtime.WindowsRuntime
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public class PitwallW {
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr l);
  public delegate bool EnumWindowsProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr a, int x, int y, int cx, int cy, uint f);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint f);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
"@

# ---- WinRT OCR ----
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($t,$rt){ $m=$asTaskGeneric.MakeGenericMethod($rt); $nt=$m.Invoke($null,@($t)); $nt.Wait(-1)|Out-Null; $nt.Result }
[void][Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
[void][Windows.Media.Ocr.OcrEngine,Windows.Media.Ocr,ContentType=WindowsRuntime]
$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if(-not $engine){ Fail "no OCR engine for user languages" }

function Ocr-Words([System.Drawing.Bitmap]$bmp){
  $tmp=Join-Path $env:TEMP ("pitwallocr_"+[guid]::NewGuid().ToString("N")+".png"); $bmp.Save($tmp,[System.Drawing.Imaging.ImageFormat]::Png)
  try {
    $f=Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($tmp)) ([Windows.Storage.StorageFile])
    $st=Await ($f.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $dec=Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($st)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $sb=Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $res=Await ($engine.RecognizeAsync($sb)) ([Windows.Media.Ocr.OcrResult])
    $st.Dispose()
  } finally { Remove-Item $tmp -ErrorAction SilentlyContinue }
  $words=@(); foreach($ln in $res.Lines){ foreach($w in $ln.Words){ $r=$w.BoundingRect
    $words+=[pscustomobject]@{Text=$w.Text;X=[int]$r.X;Y=[int]($r.Y+$r.Height/2)} } }
  ,$words
}
function Crop-Scale([System.Drawing.Bitmap]$src,[int]$x,[int]$y,[int]$w,[int]$h,[int]$scale){
  $x=[math]::Max(0,$x);$y=[math]::Max(0,$y);$w=[math]::Min($w,$src.Width-$x);$h=[math]::Min($h,$src.Height-$y)
  if($w -le 0 -or $h -le 0){return $null}
  $out=New-Object System.Drawing.Bitmap(($w*$scale),($h*$scale)); $g=[System.Drawing.Graphics]::FromImage($out)
  $g.InterpolationMode=[System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $g.DrawImage($src,(New-Object System.Drawing.Rectangle(0,0,($w*$scale),($h*$scale))),$x,$y,$w,$h,[System.Drawing.GraphicsUnit]::Pixel)
  $g.Dispose(); ,$out
}

# ---- capture off-screen ----
if(-not (Test-Path $ClaudeExe)){ Fail "claude.exe not found at $ClaudeExe" }
$proc=Start-Process -FilePath $ClaudeExe -ArgumentList '/usage' -WorkingDirectory $WorkDir -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 3
# Track ONLY our spawned process + its descendant tree ($script:set), seeded from OUR
# pid alone. We never use a "claude pids that appeared since launch" rule: a real claude
# session a user opens while we capture would match that rule and then get grabbed, moved
# off-screen, or killed. By keying off our own process tree, a sibling session is never
# touched (its parent isn't in our tree). (Ivan HIGH, 2026-06-08)
$script:set=New-Object System.Collections.Generic.HashSet[int]; [void]$script:set.Add([int]$proc.Id)
$hwnd=[IntPtr]::Zero
for($try=0;$try -lt 25 -and $hwnd -eq [IntPtr]::Zero;$try++){
  $all=Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId
  for($i=0;$i -lt 5;$i++){ $add=$false; foreach($pp in $all){ if($script:set.Contains([int]$pp.ParentProcessId) -and -not $script:set.Contains([int]$pp.ProcessId)){[void]$script:set.Add([int]$pp.ProcessId);$add=$true} }; if(-not $add){break} }
  $script:_hit=[IntPtr]::Zero
  $cb=[PitwallW+EnumWindowsProc]{ param($h,$l)
    $wp=0; [PitwallW]::GetWindowThreadProcessId($h,[ref]$wp)|Out-Null
    if($script:set.Contains([int]$wp)){ $cn=New-Object System.Text.StringBuilder 128; [PitwallW]::GetClassName($h,$cn,128)|Out-Null
      if($cn.ToString() -eq 'ConsoleWindowClass'){ $script:_hit=$h } }
    return $true }
  [PitwallW]::EnumWindows($cb,[IntPtr]::Zero)|Out-Null
  if($script:_hit -ne [IntPtr]::Zero){ $hwnd=$script:_hit; break }
  Start-Sleep -Milliseconds 700
}
function Kill-New {
  # Tear down ONLY our spawned tree, never a sibling claude session. Re-walk descendants
  # fresh first, in case the session spawned children after the find loop. (Ivan HIGH)
  try {
    $all=Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId
    for($i=0;$i -lt 6;$i++){ $add=$false; foreach($pp in $all){ if($script:set.Contains([int]$pp.ParentProcessId) -and -not $script:set.Contains([int]$pp.ProcessId)){[void]$script:set.Add([int]$pp.ProcessId);$add=$true} }; if(-not $add){break} }
  } catch {}
  foreach($k in @($script:set)){ try{Stop-Process -Id $k -Force -ErrorAction Stop}catch{} }
}
if($hwnd -eq [IntPtr]::Zero){ Kill-New; Fail "console window for spawned claude not found" }

[PitwallW]::SetWindowPos($hwnd,[IntPtr]::Zero,-32000,-32000,1500,1000,(0x0010 -bor 0x0004 -bor 0x0040))|Out-Null
[PitwallW]::ShowWindow($hwnd,4)|Out-Null   # SW_SHOWNOACTIVATE
Start-Sleep -Seconds $RenderWaitSec
$r=New-Object PitwallW+RECT; [PitwallW]::GetWindowRect($hwnd,[ref]$r)|Out-Null
$bw=$r.Right-$r.Left; $bh=$r.Bottom-$r.Top
if($bw -lt 200 -or $bh -lt 200){ Kill-New; Fail "captured window too small (${bw}x${bh})" }
$img=New-Object System.Drawing.Bitmap($bw,$bh); $gfx=[System.Drawing.Graphics]::FromImage($img)
$hdc=$gfx.GetHdc(); $ok=[PitwallW]::PrintWindow($hwnd,$hdc,0x00000002); $gfx.ReleaseHdc($hdc); $gfx.Dispose()
Kill-New
if(-not $ok){ Fail "PrintWindow failed" }
if($SaveDebug){ $img.Save((Join-Path $env:TEMP "pitwall_usage_capture.png"),[System.Drawing.Imaging.ImageFormat]::Png) }

# ---- extract ----
$iw=$img.Width;$ih=$img.Height
$rect=New-Object System.Drawing.Rectangle(0,0,$iw,$ih)
$d=$img.LockBits($rect,[System.Drawing.Imaging.ImageLockMode]::ReadOnly,[System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
$buf=New-Object byte[] ($iw*$ih*4); [System.Runtime.InteropServices.Marshal]::Copy($d.Scan0,$buf,0,$buf.Length); $img.UnlockBits($d)
function Bright([int]$x,[int]$y){ $i=($y*$iw+$x)*4; (0.114*$buf[$i]+0.587*$buf[$i+1]+0.299*$buf[$i+2]) }

$whole=Crop-Scale $img 0 0 $iw $ih 3; $wb=Ocr-Words $whole; $whole.Dispose()
if(-not $wb -or $wb.Count -eq 0){ Fail "OCR returned no text (panel may not have rendered)" }
$sw=$wb|ForEach-Object{[pscustomobject]@{Text=$_.Text;X=[int]($_.X/3);Y=[int]($_.Y/3)}}
$rows=@(); foreach($w in ($sw|Sort-Object Y)){
  $row=$rows|Where-Object{[math]::Abs($_.Y-$w.Y)-le 8}|Select-Object -First 1
  if($row){[void]$row.Items.Add($w);$row.Y=[int](($row.Y+$w.Y)/2)} else {$rows+=[pscustomobject]@{Y=$w.Y;Items=[System.Collections.ArrayList]@($w)}} }
$rj=$rows|Sort-Object Y|ForEach-Object{[pscustomobject]@{Y=$_.Y;Text=(($_.Items|Sort-Object X|ForEach-Object Text)-join ' ')}}
function RowY([string]$rx,[int]$after=0){($rj|Where-Object{$_.Text -match $rx -and $_.Y -ge $after}|Select-Object -First 1).Y}

function BarEnd([int]$y){ $bg=35;$lim=[int]($iw*0.45);$rs=-1;$bs=-1;$be=-1
  for($x=0;$x -lt $lim;$x++){ $on=(Bright $x $y) -gt $bg
    if($on){ if($rs -lt 0){$rs=$x} } else { if($rs -ge 0){ if(($x-1-$rs)-gt($be-$bs)){$bs=$rs;$be=$x-1};$rs=-1 } } }
  if($rs -ge 0 -and (($lim-1-$rs)-gt($be-$bs))){$be=$lim-1}; $be }

# % between header and its Resets line, read from the bar-free region right of the bar
function PctFor([int]$hy){
  if(-not $hy){return $null}
  $ry=RowY 'Resets' ($hy+4); if(-not $ry){$ry=$hy+32}
  $by=[int](($hy+$ry)/2); $edges=@(); foreach($dy in -4,-2,0,2,4){ $edges+=(BarEnd ($by+$dy)) }
  $be=[int](($edges|Measure-Object -Maximum).Maximum)
  $strip=Crop-Scale $img ($be+6) ($by-13) 200 26 6
  if(-not $strip){return $null}
  $j=($((Ocr-Words $strip)|Sort-Object X|ForEach-Object Text) -join ' '); $strip.Dispose()
  if($j -match '(\d{1,3})\s*%'){ $v=[int]$matches[1]; if($v -ge 0 -and $v -le 100){return $v} }
  return $null
}

# higher-zoom re-OCR of a Resets row; returns cleaned "11:10am" or "Jun 10, 5am"
function ResetFor([int]$hy){
  if(-not $hy){return $null}
  $ry=RowY 'Resets' ($hy+4); if(-not $ry){return $null}
  $strip=Crop-Scale $img 0 ($ry-13) ([int]($iw*0.42)) 26 5
  $t=''
  if($strip){ $t=($((Ocr-Words $strip)|Sort-Object X|ForEach-Object Text) -join ' '); $strip.Dispose() }
  if(-not $t){ $t=(RowY 'Resets' ($hy+4)) }
  # strip wrapper text + tz, fix common letter-for-digit in the time token
  $t=$t -replace '(?i)resets','' -replace '\(America[^)]*\)?','' -replace '[|]','' -replace '\s+',' '
  $t=$t.Trim()
  # repair a digit misread immediately before am/pm (e.g. Sam->5am, Oam->0am, lam->1am)
  $t=[regex]::Replace($t,'(?i)\b([SsOolIBZg])(\s?[ap]m)\b',{ param($m)
      $map=@{'s'='5';'o'='0';'l'='1';'i'='1';'b'='8';'z'='2';'g'='9'}
      $map[$m.Groups[1].Value.ToLower()] + $m.Groups[2].Value })
  $t=$t -replace 'Ange1es','Angeles'
  $t.Trim()
}

$out=[ordered]@{ ok=$true }
$out.session_pct   = PctFor (RowY 'Current session')
$out.session_reset = ResetFor (RowY 'Current session')
$out.weekall_pct   = PctFor (RowY 'all models')
$out.weekall_reset = ResetFor (RowY 'all models')
$out.sonnet_pct    = PctFor (RowY 'Sonnet only')
$img.Dispose()

if($out.session_pct -eq $null -and $out.weekall_pct -eq $null -and $out.sonnet_pct -eq $null){
  Fail "no percentages parsed from panel"
}
[Console]::Out.WriteLine(($out | ConvertTo-Json -Compress))
