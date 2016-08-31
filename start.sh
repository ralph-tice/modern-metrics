#!/usr/bin/bash

# dumbest but quickest way to wait for other hosts to come up before we try to connect to them
sleep 10

/usr/local/bin/pcp2es.py -h localhost -g localhost:9200 kernel
