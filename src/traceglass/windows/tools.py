"""Native schemas let the model discover Windows APIs without host credentials."""
import asyncio
from typing import Literal

from agentscope.tool import FunctionTool
from traceglass.integrations.rea_workflows import response


def windows_tools(service, user_id, workspace_id, root):
    if not service.config.allowed(user_id):
        return []

    async def invoke(function, *args, **kwargs):
        try:
            result = await asyncio.to_thread(function, *args, **kwargs)
            return response(result, error=result.get('state') in
                            {'failed', 'timeout', 'cancelled', 'unknown', 'unavailable', 'output_limit'})
        except (LookupError, ValueError, PermissionError) as exc:
            return response({'state': 'rejected', 'error_type': type(exc).__name__,
                'message': str(exc)}, error=True)
        except RuntimeError as exc:
            # Only local policy errors are safe to show. SDK faults may include
            # account details, so transport errors use the generic response.
            message = str(exc)
            if message.startswith('Windows VM is busy'):
                admission = {'execution_started': False} if function == service.submit else {}
                return response({'state': 'busy', 'message': message, **admission,
                    'capacity': service.capacity(user_id),
                    'next_step': 'Query WindowsVmStatus and finish/collect your own jobs. '
                                 'Do not reset other tasks or replay a previously started job.'}, error=True)
            if any(message.startswith(prefix) for prefix in ('Windows VM is busy', 'A debugger operation is pending',
                    'Collect Windows results', 'This task does not hold', 'Windows result is not available',
                    'Windows job changed concurrently')):
                return response({'state': 'rejected', 'message': message}, error=True)
            return response({'state': 'unavailable', 'error_type': type(exc).__name__,
                'message': 'Windows operation unavailable. Query task status before retrying.'}, error=True)
        except Exception as exc:
            return response({'state': 'unavailable', 'error_type': type(exc).__name__,
                'message': 'Windows operation failed. Check guest readiness, task status and administrator configuration. '
                           'Do not replay an execution after an ambiguous failure.'}, error=True)

    async def capabilities():
        """Check the configured Windows VM through ESXi/VMware Tools and probe installed tool paths. No target is executed. Reports x64dbg automation readiness separately from GUI installation. Returns a job_id if the probe is still running; query WindowsJobStatus. Windows access is administrator opt-in and does not change Docker networking."""
        return await invoke(service.capabilities, user_id, workspace_id, root)

    async def vm_status():
        """Read configured Windows task capacity and baseline recovery state without contacting ESXi, launching a process or taking a slot. Returns max_concurrent_jobs, occupied_slots, available_slots, exclusive_task, baseline_dirty, recovery_state and auto_reset_on_idle. No other account/workspace job identifiers are exposed. A busy VM can still accept shared tasks when slots remain; an exclusive task or baseline recovery blocks admission."""
        return await invoke(service.capacity, user_id)

    async def run_tool(sample_path: str,
                       profile: Literal['die', 'rizin', 'upx_test', 'wasm2wat', 'jadx', 'execute', 'debug_x64', 'debug_x32'],
                       timeout: int = 120, arguments: list[str] | None = None,
                       isolation: Literal['auto', 'shared', 'exclusive'] = 'auto'):
        """Upload a current-workspace file with verified SHA-256 and start a fixed Windows tool profile. sample_path is relative to this workspace or /workspace. die/rizin/upx_test/wasm2wat/jadx are static; execute/debug_x64/debug_x32 execute target code and require administrator enablement plus a clean baseline. arguments are literal argv for execute only. isolation=auto shares static profiles and makes dynamic profiles exclusive. Use isolation=shared for authorized ordinary cooperative programs whose system side effects permit concurrent debugging and that leave no background children; malware, unknown targets, system-changing programs and programs starting background processes/services must use exclusive. Shared jobs use independent directories, TEMP and debugger processes but share the OS. Check WindowsVmStatus for capacity. Returns job_id; poll WindowsJobStatus, close your debugger via WindowsDebug, then WindowsCollectResults to free the slot. Whole-VM recovery waits for all tasks; auto_reset_on_idle is administrator-controlled. Never reset another task or resubmit an ambiguous execution."""
        return await invoke(service.submit, user_id, workspace_id, root, sample_path, profile,
                            timeout=timeout, arguments=arguments, isolation=None if isolation == 'auto' else isolation)

    async def status(job_id: str):
        """Read the state and result of a Windows job owned by this account/workspace. running/unknown are not success. A process exit without a valid matching result remains unknown; do not replay. Completed debug jobs can retain an active debugger session. Outputs are guest observations, not a verified benign/malicious verdict."""
        return await invoke(service.status, user_id, workspace_id, job_id)

    async def collect(job_id: str):
        """Download bounded Windows job logs/reports into reports/windows/<job_id> in this workspace. Returns artifact SHA-256, manifest path, slot_released and capacity. Shared terminal jobs release their slot after collection; an open debugger or unresolved operation keeps its slot. With administrator auto_reset_on_idle enabled, the last fully closed/collected task triggers fixed-baseline recovery; otherwise report recovery required and use WindowsResetGuest only with existing recovery authorization. Collected local evidence remains retrievable after successful or failed restore without guest access; uncollected_operations reports operations absent from that saved report. Read the files before drawing conclusions."""
        return await invoke(service.collect, user_id, workspace_id, root, job_id)

    async def cancel(job_id: str):
        """Request cancellation of this workspace's Windows command job only. The guest worker terminates its child process tree; poll WindowsJobStatus and collect results to release a shared slot. For a debugger use WindowsDebug(action='close'). Never cancel another task or reset the VM while siblings remain."""
        return await invoke(service.cancel, user_id, workspace_id, job_id)

    async def debug(job_id: str,
                    action: Literal['status', 'registers', 'breakpoint', 'step', 'continue', 'pause', 'memory', 'dump', 'close'],
                    address: str = '', length: int = 256, count: int = 1):
        """Submit a typed operation to the fresh x64dbg session opened by this workspace's debug_x64/debug_x32 job. Requires x64dbg Automate plugin/client inside Windows. Query WindowsJobStatus operations for the result before submitting another operation; never replay a pending or unknown operation. breakpoint/memory/dump require hexadecimal address (0x...). length is 1..65536 bytes; count is 1..100 instructions. continue resumes asynchronously; status checks running/paused state. memory returns base64; dump writes a bounded memory.bin range retrieved by WindowsCollectResults. No arbitrary debugger commands or attachment to unrelated processes."""
        return await invoke(service.debug, user_id, workspace_id, job_id, action,
                            address=address, length=length, count=count)

    async def reset(job_id: str, discard_pending: bool = False):
        """Revert Windows to the administrator-configured baseline after all other tasks have closed and collected. A shared task cannot reset over another task, even with discard_pending=true. Requires this workspace's current task lease/recovery claim and recovery authorization unless administrator auto recovery already applies. Collect results and close the debugger first. discard_pending=true explicitly abandons this task's unresolved results/processes; never replay them. Revert ends guest processes and discards guest changes; no snapshot name or VM identity is accepted from the model."""
        return await invoke(service.reset, user_id, workspace_id, job_id, discard_pending=discard_pending)

    return [FunctionTool(capabilities, name='WindowsCapabilities', is_read_only=False),
            FunctionTool(vm_status, name='WindowsVmStatus', is_read_only=True),
            FunctionTool(run_tool, name='WindowsRunTool', is_read_only=False),
            FunctionTool(status, name='WindowsJobStatus', is_read_only=True),
            FunctionTool(collect, name='WindowsCollectResults', is_read_only=False),
            FunctionTool(cancel, name='WindowsCancelJob', is_read_only=False),
            FunctionTool(debug, name='WindowsDebug', is_read_only=False),
            FunctionTool(reset, name='WindowsResetGuest', is_read_only=False)]
