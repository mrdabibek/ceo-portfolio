$dirs = @('portfolio-57-windows11-clone','portfolio-58-file-terminal','portfolio-59-macos-clone','portfolio-60-swift-showcase','portfolio-61-platformer-game','portfolio-62-racer-game')
foreach ($d in $dirs) {
  $t = Get-Content "$d\index.html" -Raw
  $lines = $t.Split("`n").Count
  $open = ([regex]::Matches($t, '<div')).Count
  $close = ([regex]::Matches($t, '</div>')).Count
  $ok = if ($lines -ge 550 -and $lines -le 850 -and $open -eq $close) { 'OK' } else { 'CHECK' }
  Write-Output ("$d lines=$lines divs=$open/$close $ok")
}
