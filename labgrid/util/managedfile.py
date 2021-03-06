import hashlib
import logging
import os
import subprocess

import attr

from .helper import get_user
from .ssh import sshmanager
from ..resource.common import Resource, NetworkResource


@attr.s
class ManagedFile:
    """ The ManagedFile allows the synchronisation of a file to a remote host.
    It has to be created with the to be synced file and the target resource as
    argument:

    ::
        from labgrid.util.managedfile import ManagedFile

        ManagedFile("/tmp/examplefile", <your-resource>)


    Synchronisation is done with the sync_to_resource method.
    """
    local_path = attr.ib(
        validator=attr.validators.instance_of(str),
        converter=lambda x: os.path.abspath(str(x))
    )
    resource = attr.ib(
        validator=attr.validators.instance_of(Resource),
    )
    detect_nfs = attr.ib(default=True, validator=attr.validators.instance_of(bool))

    def __attrs_post_init__(self):
        if not os.path.isfile(self.local_path):
            raise FileNotFoundError("Local file {} not found".format(self.local_path))
        self.logger = logging.getLogger("{}".format(self))
        self.hash = None
        self.rpath = None
        self._on_nfs_cached = None

    def sync_to_resource(self):
        """sync the file to the host specified in a resource

        Raises:
            ExecutionError: if the SSH connection/copy fails
        """
        if isinstance(self.resource, NetworkResource):
            host = self.resource.host
            conn = sshmanager.open(host)

            if self._on_nfs(conn):
                return # nothing to do

            self.rpath = "/tmp/labgrid-{user}/{hash}/".format(
                user=get_user(), hash=self.get_hash()
            )
            conn.run_check("mkdir -p {}".format(self.rpath))
            conn.put_file(
                self.local_path,
                "{}{}".format(self.rpath, os.path.basename(self.local_path))
            )

    def _on_nfs(self, conn):
        if self._on_nfs_cached is not None:
            return self._on_nfs_cached

        if not self.detect_nfs:
            return False

        self._on_nfs_cached = False

        fmt = "inode=%i,size=%s,birth=%W,modified=%Y"
        local = subprocess.run(["stat", "--format", fmt, self.local_path],
                               stdout=subprocess.PIPE)
        if local.returncode != 0:
            self.logger.debug("local: stat: unsuccessful error code %d", local.returncode)
            return False

        remote = conn.run("stat --format '{}' {}".format(fmt, self.local_path),
                          decodeerrors="backslashreplace")
        if remote[2] != 0:
            self.logger.debug("remote: stat: unsuccessful error code %d", remote[2])
            return False

        localout = local.stdout.decode("utf-8", "backslashreplace").split('\n')
        localout.pop() # remove trailing empty element

        if remote[0] != localout:
            self.logger.debug("state: local (%s) and remote (%s) output don't match",
                              remote[0], localout)
            return False

        self.rpath = os.path.dirname(self.local_path) + "/"
        self._on_nfs_cached = True

        return True

    def get_remote_path(self):
        """Retrieve the remote file path

        Returns:
            str: path to the file on the remote host
        """
        if isinstance(self.resource, NetworkResource):
            return "{}{}".format(self.rpath, os.path.basename(self.local_path))

        return self.local_path

    def get_hash(self):
        """Retrieve the hash of the file

        Returns:
            str: SHA256 hexdigest of the file
        """

        if self.hash is not None:
            return self.hash

        hasher = hashlib.sha256()
        with open(self.local_path, 'rb') as f:
            for block in iter(lambda: f.read(1048576), b''):
                hasher.update(block)
        self.hash = hasher.hexdigest()

        return self.hash
