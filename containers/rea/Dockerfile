ARG BASE_IMAGE=local/rea-mcp:5.0.0-ghidra12.1.4-hopper6.4.2-amd64
FROM ${BASE_IMAGE}
USER root
COPY rea-agents-6.1.0.tgz /tmp/rea-agents.tgz
RUN /opt/node/bin/npm install --prefix /opt/rea --save-exact --ignore-scripts --no-audit --no-fund /tmp/rea-agents.tgz \
    && rm /tmp/rea-agents.tgz \
    && /opt/node/bin/npm cache clean --force \
    && python3 /usr/local/lib/rea-container/apply-rea-patches.py /opt/rea/node_modules/rea-agents/scripts/hopper-demo-x11.py
USER rea
