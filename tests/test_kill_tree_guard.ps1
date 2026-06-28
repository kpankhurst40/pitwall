# Regression guard for the 2026-06-27 "Pitwall sync killed all open CLIs" bug.
#
# read_usage.ps1 kills the /usage capture's own process tree by walking Windows'
# parent->child graph from the spawned pid. Windows recycles pids and leaves an orphaned
# process's recorded ParentProcessId pointing at the now-free pid, so a freshly spawned
# claude /usage could inherit a pid that a PRE-EXISTING orphaned session still names as its
# parent - and the kill then swept those unrelated live sessions away.
#
# The fix: Build-Tree only adopts descendants created AT/AFTER the spawn ($since). This test
# builds a synthetic graph where an OLD orphan records our root pid as its parent (the
# recycle collision) and asserts that orphan (and its child) are NOT swept in, while the
# genuine, newer descendants ARE. If the $since gate is ever removed, this fails loudly.

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path (Split-Path -Parent $here) 'scripts\read_usage.ps1'

# Dot-source the script in define-only mode: defines Build-Tree, spawns nothing.
. $script -DefineOnly

$root  = 1000
$since  = [datetime]'2026-06-27T17:00:00'    # our spawn instant
$young  = $since.AddSeconds(5)               # genuine descendants: created after the spawn
$young2 = $since.AddSeconds(20)              # a deeper genuine descendant, created later still
$old    = $since.AddHours(-1)               # pre-existing orphan: created an hour earlier

$all = @(
  # --- our genuine tree (all created at/after the spawn, each child after its parent) ---
  [pscustomobject]@{ ProcessId=1000; ParentProcessId=42;   CreationDate=$since  }  # root (the spawn)
  [pscustomobject]@{ ProcessId=1001; ParentProcessId=1000; CreationDate=$young  }  # real child   cmd
  [pscustomobject]@{ ProcessId=1002; ParentProcessId=1001; CreationDate=$young  }  # real gchild  node
  [pscustomobject]@{ ProcessId=3000; ParentProcessId=1002; CreationDate=$young2 }  # real deep descendant
  # --- depth-1 stale-parent orphan (the OBSERVED bug): pid 1000 recycled to our root ---
  [pscustomobject]@{ ProcessId=2000; ParentProcessId=1000; CreationDate=$old    }  # pre-existing orphan
  [pscustomobject]@{ ProcessId=2001; ParentProcessId=2000; CreationDate=$old    }  # orphan's live child
  # --- depth-2 within-window recycle (Ivan MEDIUM-2): unrelated U (3001) created AFTER the
  #     spawn, whose parent pid 3000 got recycled to our deep descendant. Monotonic edge must
  #     reject it: U is OLDER than pid 3000's current (our) occupant. ---
  [pscustomobject]@{ ProcessId=3001; ParentProcessId=3000; CreationDate=$young  }  # unrelated live CLI
)

$tree = Build-Tree $all $root $since

$fail = @()
foreach ($must in 1000,1001,1002,3000) { if (-not $tree.ContainsKey($must)) { $fail += "missing genuine pid $must" } }
foreach ($never in 2000,2001)          { if ($tree.ContainsKey($never))     { $fail += "swept in pre-existing orphan pid $never (recycled-pid kill bug)" } }
if ($tree.ContainsKey(3001))           { $fail += "swept in depth-2 within-window recycle pid 3001 (monotonic-edge guard failed)" }

if ($fail.Count) {
  Write-Host "FAIL: Build-Tree guard regression:" -ForegroundColor Red
  $fail | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
  exit 1
}
Write-Host "PASS: Build-Tree excludes recycled-pid orphans, keeps genuine descendants." -ForegroundColor Green
exit 0
