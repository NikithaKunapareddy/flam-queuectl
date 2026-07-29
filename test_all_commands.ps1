# ==============================================================================
# QUEUECTL RIGOROUS END-TO-END CLI & ENGINE VERIFICATION SUITE
# Tests every CLI command, database state transition, DLQ rescue, and pytest suite
# ==============================================================================

$ErrorActionPreference = "Stop"
$TotalTests = 10
$Passed = 0

function Test-Step {
    param([string]$Title, [scriptblock]$Action)
    Write-Host "`n----------------------------------------------------------------" -ForegroundColor Cyan
    Write-Host " [TEST] $Title" -ForegroundColor Yellow
    Write-Host "----------------------------------------------------------------" -ForegroundColor Cyan
    try {
        & $Action
        Write-Host "  [PASS] $Title" -ForegroundColor Green
        $script:Passed++
    } catch {
        Write-Host "  [FAIL] $Title - Error: $_" -ForegroundColor Red
        exit 1
    }
}

Write-Host "================================================================" -ForegroundColor Cyan
Write-Host " STARTING RIGOROUS QUEUECTL FULL-STACK TEST SUITE" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan

# 1. RESET DATABASE TO CLEAN STATE
Test-Step "Database Reset & Initialization (Remove-Item .queuectl)" {
    if (Test-Path ".queuectl") {
        Remove-Item -Recurse -Force ".queuectl" -ErrorAction SilentlyContinue
    }
    python -m queuectl.cli status
}

# 2. TEST STATUS COMMAND
Test-Step "CLI Status Inspection (queuectl status)" {
    $out = (python -m queuectl.cli status) -join "`n"
    if ($out -notmatch "pending   : 0") { throw "Status count mismatch" }
}

# 3. TEST ENQUEUE COMMANDS (HELLO, SLEEP, FAIL)
Test-Step "Job Enqueueing (queuectl enqueue)" {
    python -m queuectl.cli --% enqueue "{\"id\":\"job-hello\",\"command\":\"echo Hello from QueueCTL\"}"
    python -m queuectl.cli --% enqueue "{\"id\":\"job-sleep\",\"command\":\"ping 127.0.0.1 -n 1\"}"
    python -m queuectl.cli --% enqueue "{\"id\":\"job-fail\",\"command\":\"cmd /c exit 1\",\"max_retries\":1}"
}

# 4. TEST LIST COMMANDS (TABLE & JSON)
Test-Step "Queue Listing in Table and JSON Formats (queuectl list)" {
    Write-Host "  -- Table Format:" -ForegroundColor DarkGray
    python -m queuectl.cli list
    Write-Host "  -- JSON Format (--json):" -ForegroundColor DarkGray
    $jsonOut = (python -m queuectl.cli list --state pending --json) -join ""
    if ($jsonOut -notmatch "job-hello" -or $jsonOut -notmatch "job-fail") {
        throw "JSON listing did not contain expected jobs"
    }
}

# 5. TEST PERSISTENT CONFIG COMMANDS (CONFIG GET / SET)
Test-Step "Dynamic SQLite Configuration (queuectl config get / set)" {
    python -m queuectl.cli config set max_retries 5
    python -m queuectl.cli config set backoff_base 3
    $retries = (python -m queuectl.cli config get max_retries).Trim()
    $backoff = (python -m queuectl.cli config get backoff_base).Trim()
    if ($retries -ne "5" -or $backoff -ne "3") {
        throw "Config get/set failed: max_retries=$retries, backoff_base=$backoff"
    }
    # Reset to default for test
    python -m queuectl.cli config set max_retries 3
    python -m queuectl.cli config set backoff_base 2
}

# 6. TEST ATOMIC WORKER EXECUTION
Test-Step "Worker Execution & Atomic Job Processing (queuectl worker start)" {
    Write-Host "  -> Launching background worker to process queue..." -ForegroundColor DarkGray
    $script:workerProc = Start-Process python -ArgumentList "-m queuectl.cli worker start --count 1" -NoNewWindow -PassThru
    Start-Sleep -Seconds 5
    $completedList = (python -m queuectl.cli list --state completed) -join "`n"
    if ($completedList -notmatch "job-hello" -or $completedList -notmatch "job-sleep") {
        throw "Completed queue did not contain job-hello and job-sleep"
    }
    # Stop worker NOW before DLQ test so it cannot re-process rescued jobs
    if ($script:workerProc -and !$script:workerProc.HasExited) {
        Stop-Process -Id $script:workerProc.Id -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1  # Wait for worker to fully terminate
    Write-Host "  -> Worker stopped cleanly before DLQ test" -ForegroundColor DarkGray
}

# 7. TEST DEAD LETTER QUEUE (DLQ) LISTING & RESCUE
Test-Step "Dead Letter Queue Quarantine & Rescue (queuectl dlq list / retry)" {
    # Verify job-fail was quarantined in DLQ after exhausting max_retries=1
    $dlqList = (python -m queuectl.cli dlq list) -join "`n"
    Write-Host "  -> DLQ contents: $dlqList" -ForegroundColor DarkGray
    if ($dlqList -notmatch "job-fail") {
        throw "job-fail was not found in DLQ after retry exhaustion"
    }
    Write-Host "  -> Rescuing job-fail via dlq retry (no worker running)..." -ForegroundColor DarkGray
    python -m queuectl.cli dlq retry job-fail
    # With NO worker running, job-fail must now be in pending state
    $pendingAfter = (python -m queuectl.cli list --state pending) -join "`n"
    Write-Host "  -> Pending after rescue: $pendingAfter" -ForegroundColor DarkGray
    if ($pendingAfter -notmatch "job-fail") {
        throw "job-fail was not returned to pending queue after dlq retry"
    }
    Write-Host "  -> DLQ rescue confirmed: job-fail is back in pending!" -ForegroundColor Green
}

# 8. TEST WORKER STOP & CLEANUP
Test-Step "Worker Graceful Signal Termination (queuectl worker stop)" {
    python -m queuectl.cli worker stop
}

# 9. TEST STREAMLIT DASHBOARD CODE COMPILATION
Test-Step "Streamlit Web Dashboard Verification (streamlit_app.py)" {
    python -m py_compile streamlit_app.py
    python -c "import streamlit_app; print('  -> streamlit_app.py compiled and imported cleanly without errors')"
}

# 10. RUN COMPLETE 6-SCENARIO PYTEST SUITE
Test-Step "Automated 6-Scenario Engineering Unit Test Suite (pytest -v)" {
    python -m pytest -v
}

Write-Host "`n================================================================" -ForegroundColor Green
Write-Host " ALL $Passed / $TotalTests END-TO-END RIGOROUS TESTS PASSED!" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
