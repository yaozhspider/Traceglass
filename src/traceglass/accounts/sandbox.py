"""Tenant container policy; AgentScope retains gateway, tools and lifecycle."""
import os
from agentscope.workspace import DockerWorkspace
from agentscope.workspace._docker._docker_workspace import DockerBackend, CONTAINER_WORKDIR


class AccountDockerWorkspace(DockerWorkspace):
    async def _create_and_start_container(self):
        # Native 2.0.6 has no container-policy hook. Adapt only creation: its
        # gateway uses docker exec, so no network access or published port is needed.
        if not self.host_workdir:
            raise ValueError('Account workspaces require an owned bind mount')
        os.makedirs(self.host_workdir, exist_ok=True)
        config = {
            'Image': self._image_tag, 'Cmd': ['sleep', 'infinity'],
            'WorkingDir': CONTAINER_WORKDIR,
            'Labels': {'agentscope.workspace':'true', 'agentscope.workspace.id':self.workspace_id,
                       'traceglass.account-isolated':'true'},
            'HostConfig': {
                'Binds':[f'{os.path.abspath(self.host_workdir)}:{CONTAINER_WORKDIR}:rw'],
                'NetworkMode':'none', 'CapDrop':['ALL'],
                'SecurityOpt':['no-new-privileges:true'],
                'Memory':4*1024**3, 'MemorySwap':4*1024**3,
                'NanoCpus':2_000_000_000, 'PidsLimit':512,
            },
        }
        if self.env:
            config['Env']=[f'{key}={value}' for key,value in self.env.items()]
        self._container=await self._client.containers.create_or_replace(
            name=f'as_ws_{self.workspace_id}',config=config)
        await self._container.start()
        self._backend=DockerBackend(self._container,CONTAINER_WORKDIR)
