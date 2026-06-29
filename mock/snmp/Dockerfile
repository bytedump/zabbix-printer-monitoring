# Mock SNMP agent for the demo: serves fake Printer-MIB toner data so the whole
# stack can run without physical printers. snmpsim-lextudio is the maintained
# fork of snmpsim; building from the official python base keeps it auditable.
FROM python:3.12-slim

RUN pip install --no-cache-dir snmpsim-lextudio

# Run as a non-root user (see rules/docker.md). HOME points at /tmp so the
# process still has a writable spot when the container runs with read_only.
RUN useradd --create-home --uid 1000 snmpsim
USER snmpsim
ENV HOME=/tmp

# Unprivileged SNMP port (no CAP_NET_BIND needed). Zabbix polls snmp-sim:1161.
EXPOSE 1161/udp

# Community routing: each /data/<community>.snmprec answers SNMP requests whose
# community equals that file's basename (the lowercased printer name).
CMD ["snmpsim-command-responder", \
     "--data-dir=/data", \
     "--agent-udpv4-endpoint=0.0.0.0:1161", \
     "--logging-method=stderr"]
