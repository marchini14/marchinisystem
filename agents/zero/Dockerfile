FROM python:3.11-slim

RUN echo "wireshark-common wireshark-common/install-setuid boolean false" | debconf-set-selections && \
    DEBIAN_FRONTEND=noninteractive apt-get update -qq && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        git curl nmap tshark perl libnet-ssleay-perl libjson-perl libxml-writer-perl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# nikto isn't packaged for Debian trixie — install from source (Perl script, no build step)
RUN git clone --depth 1 https://github.com/sullo/nikto.git /opt/nikto && \
    ln -s /opt/nikto/program/nikto.pl /usr/local/bin/nikto && \
    chmod +x /usr/local/bin/nikto

RUN pip install --no-cache-dir slither-analyzer

ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY agent.py .

EXPOSE 8080

CMD ["python", "agent.py"]
