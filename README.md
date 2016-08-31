## Modern Metrics


Usage:

`docker-compose up`

Should be able to use vector at http://localhost:80 and see ES documents in http://localhost:9200

TODO:

1) verify document structure lends itself well to aggregation
2) make sure pcp2es is robust to transient failures ex: es node goes away/comes back
3) work our way back into pcp mainline (dependency management)
