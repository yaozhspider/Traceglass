ARG BASE_IMAGE=local/rea-mcp:5.0.0-ghidra12.1.4-hopper6.4.2-amd64
FROM ${BASE_IMAGE}
USER root
ARG REA_ARCHIVE
COPY ${REA_ARCHIVE} /tmp/rea-agents.tgz
RUN /opt/node/bin/npm install --prefix /opt/rea --save-exact --ignore-scripts --no-audit --no-fund /tmp/rea-agents.tgz \
    && rm /tmp/rea-agents.tgz \
    && /opt/node/bin/npm cache clean --force \
    && python3 /usr/local/lib/rea-container/apply-rea-patches.py /opt/rea/node_modules/rea-agents/scripts/hopper-demo-x11.py
COPY pwntools-requirements.txt /tmp/pwntools-requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/rea-pwntools \
    && /opt/rea-pwntools/bin/python -m pip install --no-cache-dir -r /tmp/pwntools-requirements.txt \
    && /opt/rea-pwntools/bin/python -m pip check \
    && rm /tmp/pwntools-requirements.txt
ENV REA_PWNTOOLS_PYTHON=/opt/rea-pwntools/bin/python
ENV PATH=/opt/rea-pwntools/bin:${PATH}
USER rea
