"""Bounded VMware Tools transfers and process calls; no guest network service."""
from contextlib import contextmanager
import ssl
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener


def transfer_url(url, host):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in ('*', host)
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or parsed.fragment):
        raise ValueError('Guest transfer URL must use the configured ESXi HTTPS origin')
    return urlunsplit(('https', host, parsed.path, parsed.query, ''))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Guest transfers do not follow redirects')


class VSphereTransport:
    def __init__(self, config):
        self.config = config

    def _context(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # Default ESXi certificates often contain localhost.localdomain. Trust
        # only the explicitly supplied certificate, obtained over verified SSH.
        context.check_hostname = False
        context.load_verify_locations(cafile=str(self.config.certificate))
        context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
        return context

    @contextmanager
    def _connection(self, *, require_guest=True):
        from pyVim.connect import SmartConnect, Disconnect
        from pyVmomi import vim
        cfg = self.config
        si = SmartConnect(host=cfg.host, user=cfg.esxi_user, pwd=cfg.esxi_password,
                          sslContext=self._context(), httpConnectionTimeout=20, connectionPoolTimeout=20)
        try:
            content = si.RetrieveContent()
            vm = vim.VirtualMachine(cfg.vm_id, si._stub)
            if vm.name != cfg.vm_name:
                raise RuntimeError('Configured Windows VM is unavailable')
            if require_guest and (str(vm.runtime.powerState) != 'poweredOn' or not vm.guest.guestOperationsReady):
                raise RuntimeError('VMware Tools Guest Operations is not ready')
            auth = vim.vm.guest.NamePasswordAuthentication(
                username=cfg.guest_user, password=cfg.guest_password, interactiveSession=False)
            yield content.guestOperationsManager, vm, auth
        finally:
            Disconnect(si)

    def probe(self):
        with self._connection() as (manager, vm, auth):
            manager.authManager.ValidateCredentialsInGuest(vm, auth)
            return {'vm_name': vm.name, 'vm_id': vm._moId,
                    'guest_os': vm.guest.guestFullName, 'ip_address': vm.guest.ipAddress,
                    'tools_running': str(vm.guest.toolsRunningStatus),
                    'guest_operations_ready': bool(vm.guest.guestOperationsReady)}

    def mkdir(self, path):
        from pyVmomi import vim
        with self._connection() as (manager, vm, auth):
            try:
                manager.fileManager.MakeDirectoryInGuest(vm, auth, path, True)
            except vim.fault.FileAlreadyExists:
                pass

    def _opener(self):
        return build_opener(ProxyHandler({}), HTTPSHandler(context=self._context()), NoRedirect())

    def put(self, path, data):
        from pyVmomi import vim
        if len(data) > max(self.config.max_input_bytes, 1024 * 1024):
            raise ValueError('Guest upload exceeds input budget')
        with self._connection() as (manager, vm, auth):
            url = manager.fileManager.InitiateFileTransferToGuest(
                vm, auth, path, vim.vm.guest.FileManager.FileAttributes(), len(data), True)
            request = Request(transfer_url(url, self.config.host), data=data, method='PUT',
                              headers={'Content-Type': 'application/octet-stream'})
            with self._opener().open(request, timeout=30) as result:
                if result.status not in (200, 201, 204):
                    raise RuntimeError('Guest upload failed')

    def get(self, path, limit):
        if limit < 0:
            raise ValueError('Guest download exceeds artifact budget')
        with self._connection() as (manager, vm, auth):
            info = manager.fileManager.InitiateFileTransferFromGuest(vm, auth, path)
            if info.size > limit:
                raise ValueError('Guest download exceeds artifact budget')
            with self._opener().open(transfer_url(info.url, self.config.host), timeout=30) as result:
                data = result.read(limit + 1)
            if len(data) > limit:
                raise ValueError('Guest download exceeds artifact budget')
            return data

    def start(self, executable, arguments, cwd):
        from pyVmomi import vim
        with self._connection() as (manager, vm, auth):
            return manager.processManager.StartProgramInGuest(vm, auth,
                vim.vm.guest.ProcessManager.ProgramSpec(programPath=executable,
                                                       arguments=arguments, workingDirectory=cwd))

    def process(self, pid):
        with self._connection() as (manager, vm, auth):
            processes = manager.processManager.ListProcessesInGuest(vm, auth, [pid])
            if not processes:
                return {'pid': pid, 'running': False, 'exit_code': None, 'expired': True}
            item = processes[0]
            return {'pid': pid, 'running': item.endTime is None, 'exit_code': item.exitCode}

    def terminate(self, pid):
        with self._connection() as (manager, vm, auth):
            manager.processManager.TerminateProcessInGuest(vm, auth, pid)

    @staticmethod
    def _baseline(vm, snapshot_name):
        matches = []
        def visit(nodes):
            for node in nodes:
                if node.name == snapshot_name:
                    matches.append(node.snapshot)
                visit(node.childSnapshotList)
        visit(vm.snapshot.rootSnapshotList if vm.snapshot else [])
        if len(matches) != 1:
            raise ValueError('Configured baseline snapshot must match exactly one snapshot')
        return matches[0]

    def check_baseline(self, snapshot_name):
        with self._connection(require_guest=False) as (_, vm, _auth):
            self._baseline(vm, snapshot_name)

    def restore(self, snapshot_name):
        with self._connection(require_guest=False) as (manager, vm, auth):
            task = self._baseline(vm, snapshot_name).RevertToSnapshot_Task()
            deadline = time.monotonic() + 45
            while str(task.info.state) in ('queued', 'running') and time.monotonic() < deadline:
                time.sleep(.5)
            if str(task.info.state) != 'success':
                raise RuntimeError('Baseline restore did not complete; VM lease retained')
            # A disk-only baseline can restore the VM to poweredOff. Do not
            # report a reusable VM until its guest execution channel is ready.
            if str(vm.runtime.powerState) == 'poweredOff':
                task = vm.PowerOnVM_Task()
                deadline = time.monotonic() + 45
                while str(task.info.state) in ('queued', 'running') and time.monotonic() < deadline:
                    time.sleep(.5)
                if str(task.info.state) != 'success':
                    raise RuntimeError('Restored Windows VM did not power on; VM lease retained')
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if str(vm.runtime.powerState) == 'poweredOn' and vm.guest.guestOperationsReady:
                    manager.authManager.ValidateCredentialsInGuest(vm, auth)
                    return
                time.sleep(1)
            raise RuntimeError('Restored Windows guest is not ready; VM lease retained')
