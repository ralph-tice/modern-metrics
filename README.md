## Modern Metrics

This project is an attempt at introducing a consolidated metrics pipeline for both system and application metrics.  With the existence of https://github.com/performancecopilot/parfait for JVM applications and https://github.com/performancecopilot/speed for golang, the only missing piece is for ruby/python/nodejs based backends to emit into our consolidated pipeline.

Given how a centralized statsd quickly becomes a bottleneck and most configurations emit to statsd over UDP, infrastructures are generally already prepared to run statsd on every node.

Instead, let's aim to run pmcd with statsd support via a pmda.

Visualization tools should be available via Vector for realtime and Grafana or Kibana to query Elasticsearch.  Of particular note is the ability of pcp to monitor and report on containers, the utility of which remains to be evaluated and verified.

# References
 * http://pcp.io/books/PCP_PG/html-single/#id5177140
 * http://www.pcp.io/pipermail/pcp/2015-February/006578.html
 * https://github.com/performancecopilot/speed#walkthrough

# Usage

`docker-compose up`

Should be able to use vector at http://localhost:80 and see ES documents in http://localhost:9200

# TODO

1) wire up https://github.com/performancecopilot/pcp/blob/master/src/pmdas/simple/pmdasimple.python to https://github.com/sivy/pystatsd/blob/master/pystatsd/server.py in order to ingest via statsd "protocol"

2) verify document structure lends itself well to aggregation / bulk ingestion

Nice to have:

1) make sure pcp2es is robust to transient failures ex: es node goes away/comes back

2) work our way back into pcp mainline (dependency management)

3) add kafka as a supported distribution mechanism in addition to STOMP+JMS
