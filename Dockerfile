FROM hc000/docker-pcp-all:master

RUN /usr/bin/pcp python -c "import pip; pip.main(['install', 'elasticsearch']);"

COPY pcp2es.py /usr/local/bin/pcp2es.py
COPY start.sh /usr/local/bin/start.sh

CMD /usr/local/bin/start.sh
