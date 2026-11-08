"""Linux POSIX ACL regressions mirroring the macOS extended-ACL cases.

The five macOS cases in test_public_task_status and test_bridge_recovery set ACLs
with /bin/chmod +a and skip on other hosts, so the Linux xattr branch of
agent_bridge._directory_has_acl and _clear_new_directory_acl had no regression.
These cases set real POSIX ACLs through the kernel xattr interface, so no setfacl
binary is needed. They skip unless the host is Linux and the test filesystem
accepts POSIX ACLs. Each case checks its own setup through the xattrs directly
rather than through the code under test. All agent responses are scripted; no
live model is used.
"""
import errno
import json
import os
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import agent_bridge as bridge
import task_workflow as tasks
import test_bridge_recovery
import test_public_task_status

ACCESS, DEFAULT = 'system.posix_acl_access', 'system.posix_acl_default'
_USER_OBJ, _USER, _GROUP_OBJ, _MASK, _OTHER = 0x01, 0x02, 0x04, 0x10, 0x20
_UNDEFINED_ID = 0xFFFFFFFF


def _acl_value(owner, group, other, mask, named):
    """Encode a Linux posix_acl_xattr value with one named-user entry."""
    entries = [(_USER_OBJ, owner, _UNDEFINED_ID), (_USER, named, os.getuid() + 1),
               (_GROUP_OBJ, group, _UNDEFINED_ID), (_MASK, mask, _UNDEFINED_ID),
               (_OTHER, other, _UNDEFINED_ID)]
    return struct.pack('<I', 2) + b''.join(struct.pack('<HHI', *entry) for entry in entries)


def add_acl(path, *, inherit=False):
    """Add an extended ACL without changing mode bits, like macOS chmod +a.

    The mask equals the existing group bits, so only ACL detection, not the
    mode privacy check, can observe the change. With inherit, a default ACL
    granting a named user read/execute is added for new children.
    """
    mode = os.stat(path).st_mode
    owner, group, other = (mode >> 6) & 7, (mode >> 3) & 7, mode & 7
    os.setxattr(path, ACCESS, _acl_value(owner, group, other, group, 5))
    if inherit:
        os.setxattr(path, DEFAULT, _acl_value(7, 5, 5, 7, 5))


def remove_acl(path):
    for name in (ACCESS, DEFAULT):
        try:
            os.removexattr(path, name)
        except OSError as exc:
            if exc.errno != errno.ENODATA:
                raise


def acl_names(path):
    """Independent oracle: which ACL xattrs exist, read without agent_bridge."""
    present = []
    for name in (ACCESS, DEFAULT):
        try:
            os.getxattr(path, name)
            present.append(name)
        except OSError as exc:
            if exc.errno != errno.ENODATA:
                raise
    return present


def _linux_acl_supported():
    if not sys.platform.startswith('linux') or not hasattr(os, 'setxattr'):
        return False
    try:
        with tempfile.TemporaryDirectory() as directory:
            add_acl(directory, inherit=True)
            return acl_names(directory) == [ACCESS, DEFAULT]
    except OSError:
        return False


linux_acl_only = unittest.skipUnless(
    _linux_acl_supported(), 'Linux POSIX ACL regression needs an ACL-capable temporary filesystem')


@linux_acl_only
class LinuxAclPrimitiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = temporary.name
        self.fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.fd)

    def test_plain_directory_has_no_acl(self):
        self.assertEqual([], acl_names(self.directory))
        self.assertFalse(bridge._directory_has_acl(self.fd))

    def test_access_acl_is_detected(self):
        add_acl(self.directory)
        self.assertEqual([ACCESS], acl_names(self.directory))
        self.assertTrue(bridge._directory_has_acl(self.fd))

    def test_default_only_acl_is_detected(self):
        os.setxattr(self.directory, DEFAULT, _acl_value(7, 5, 5, 7, 5))
        self.assertEqual([DEFAULT], acl_names(self.directory))
        self.assertTrue(bridge._directory_has_acl(self.fd))

    def test_clear_removes_access_and_default_acls_and_keeps_mode(self):
        before = os.stat(self.directory).st_mode
        add_acl(self.directory, inherit=True)
        bridge._clear_new_directory_acl(self.fd)
        self.assertEqual([], acl_names(self.directory))
        self.assertFalse(bridge._directory_has_acl(self.fd))
        self.assertEqual(before, os.stat(self.directory).st_mode)

    def test_uninspectable_acl_fails_closed(self):
        unsupported = OSError(errno.EOPNOTSUPP, 'ACL unsupported')
        with mock.patch.object(bridge.os, 'getxattr', side_effect=unsupported):
            with self.assertRaises(OSError):
                bridge._directory_has_acl(self.fd)


@linux_acl_only
class LinuxPublicTaskAclTests(unittest.TestCase):
    """Linux counterparts of the macOS cases in test_public_task_status."""
    _base = test_public_task_status.PublicTaskStatusTests
    setUp = _base.setUp
    fix = _base.fix
    inspect = _base.inspect

    def test_inherited_acls_removed_from_new_private_run_boundaries(self):
        self.root.chmod(0o755)
        (self.root / 'app.py').chmod(0o600)
        for name in ('tasks', 'agent-runs'):
            parent = self.root / '.proof' / name
            parent.mkdir(parents=True)
            add_acl(parent, inherit=True)
            self.assertEqual([ACCESS, DEFAULT], acl_names(parent))
        self.fix()
        for path in (self.root / '.proof/tasks/public', self.root / '.proof/agent-runs/public'):
            self.assertEqual([], acl_names(path), path)
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                self.assertEqual(0, os.fstat(fd).st_mode & 0o077)
                self.assertFalse(bridge._directory_has_acl(fd))
            finally:
                os.close(fd)
        self.assertEqual('current', self.inspect()['freshness']['state'])

    def test_existing_acl_is_preserved_and_blocks_or_marks_status_stale(self):
        self.fix()
        directory = self.root / '.proof/tasks/public'
        mode = directory.stat().st_mode
        add_acl(directory)
        self.assertEqual(mode, directory.stat().st_mode)
        self.assertEqual('stale', self.inspect()['freshness']['state'])
        result = tasks.execute(self.root, 'Make VALUE equal 2.', run_id='public', host=self.host)
        self.assertEqual('blocked', result['outcome'])
        self.assertIn('has an ACL', result['blocker']['reason'])
        self.assertEqual([ACCESS], acl_names(directory))
        remove_acl(directory)
        self.assertEqual('current', self.inspect()['freshness']['state'])

    def test_acl_added_during_verification_blocks_completion(self):
        root = self.root

        class AclChangingHost(test_public_task_status.RecordedHost):
            def run(self, role, *args, **kwargs):
                result = super().run(role, *args, **kwargs)
                if role == 'verifier':
                    add_acl(root / '.proof/agent-runs/public', inherit=True)
                return result

        self.host = AclChangingHost()
        result = tasks.execute(root, 'Make VALUE equal 2.', run_id='public', host=self.host)
        self.assertEqual([ACCESS, DEFAULT], acl_names(root / '.proof/agent-runs/public'))
        self.assertEqual('blocked', result['outcome'])
        self.assertNotIn('diff', result)
        self.assertIn('has an ACL', result['blocker']['reason'])
        state = json.loads((root / '.proof/agent-runs/public/state.json').read_text())
        self.assertNotEqual('completed_advisory', state['status'])
        self.assertIsNotNone(state['active'])
        self.assertNotEqual('current', self.inspect()['freshness']['state'])

    def test_acl_added_during_a_process_stops_it_at_next_poll(self):
        self.scope['quality'] = [['{python}', '-B', '-c', 'import time; time.sleep(10)']]
        self.profile.write_text(json.dumps({'schema_version': 1, 'plan': self.scope}))
        directory = self.root / '.proof/agent-runs/public'
        added = []

        def change_acl():
            limit = time.monotonic() + 5
            stage = directory / 'steps/000-quality-0'
            while time.monotonic() < limit and not list(stage.glob('launch-*.json')):
                time.sleep(.01)
            add_acl(directory, inherit=True)
            added.append(acl_names(directory))

        worker = threading.Thread(target=change_acl)
        worker.start()
        started = time.monotonic()
        result = tasks.execute(self.root, 'Make VALUE equal 2.', run_id='public', host=self.host)
        worker.join(timeout=6)
        self.assertFalse(worker.is_alive())
        self.assertEqual([[ACCESS, DEFAULT]], added)
        self.assertEqual('blocked', result['outcome'])
        self.assertIn('has an ACL', result['blocker']['reason'])
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual([], self.host.calls)
        cleanup = json.loads((directory / 'steps/000-quality-0/check-0/cleanup.json').read_text())
        self.assertEqual('complete', cleanup['status'])


@linux_acl_only
class LinuxRecoveryAclTests(unittest.TestCase):
    """Linux counterpart of the macOS case in test_bridge_recovery."""
    _base = test_bridge_recovery.RecoveryTests
    setUp = _base.setUp
    save_plan = _base.save_plan
    open = _base.open
    two_files = _base.two_files
    interrupt_promotion = _base.interrupt_promotion

    def test_recovery_retains_uncertain_state_if_acl_changes_at_final_status(self):
        self.two_files()
        loop = self.interrupt_promotion()
        status = loop.status

        def add_acl_before_final_status():
            add_acl(loop.directory)
            return status()

        try:
            with mock.patch.object(loop, 'status', side_effect=add_acl_before_final_status):
                with self.assertRaisesRegex(ValueError, 'ACL'):
                    loop.recover_promotion()
            self.assertEqual([ACCESS], acl_names(loop.directory))
        finally:
            remove_acl(loop.directory)
        persisted = json.loads(loop.state_path.read_text())
        self.assertEqual('blocked_uncertain', persisted['status'])
        self.assertIsNotNone(persisted['active'])


if __name__ == '__main__':
    unittest.main()
