#!/usr/bin/pcp python
#
# borrowed 8/30/2016 from https://sourceware.org/git/?p=pcpfans.git;a=blob_plain;f=src/pcp2es/pcp2es.py;hb=refs/heads/fche/pcp2es
# Copyright (C) 2014-2015 Red Hat.
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 2 of the License, or (at your
# option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
# or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
# for more details.
#
""" Relay PCP metrics to a elasticsearch server """

import re
import sys
import time
import json

from elasticsearch import Elasticsearch
from pcp import pmapi
import cpmapi as c_api

class ElasticsearchRelay(object):
    """ Sends a periodic report to elasticsearch about all instances of named
        metrics.  Knows about some of the default PCP arguments.
    """

    def describe_source(self):
        """ Return a string describing our context; apprx. inverse of
            pmapi.fromOptions
        """

        ctxtype = self.context.type
        if ctxtype == c_api.PM_CONTEXT_ARCHIVE:
            return "archive " + ", ".join(self.opts.pmGetOptionArchives())
        elif ctxtype == c_api.PM_CONTEXT_HOST:
            hosts = self.opts.pmGetOptionHosts()
            if hosts is None: # pmapi.py idiosyncracy; it has already defaulted to this
                hosts = ["local:"]
            return "host " + ", ".join(hosts)
        elif ctxtype == c_api.PM_CONTEXT_LOCAL:
            hosts = ["local:"]
            return "host " + ", ".join(hosts)
        else:
            raise pmapi.pmUsageErr

    def __init__(self):
        """ Construct object, parse command line """
        self.opts = pmapi.pmOptions()
        self.opts.pmSetShortOptions("a:O:s:T:g:i:u:m:t:h:t:D:LV?")
        self.opts.pmSetShortUsage("[options] metricname ...")
        self.opts.pmSetOptionCallback(self.option)
        self.opts.pmSetOverrideCallback(self.option_override)
        self.opts.pmSetLongOptionText("""
Description: Periodically, relay raw values of all instances of a
given hierarchies of PCP metrics to a elasticsearch/carbon server on the
network.""")
        self.opts.pmSetLongOptionHeader("Options")
        self.opts.pmSetLongOptionVersion() # -V
        self.opts.pmSetLongOptionArchive() # -a FILE
        self.opts.pmSetLongOptionOrigin() # -O TIME
        self.opts.pmSetLongOptionSamples() # -s NUMBER
        self.opts.pmSetLongOptionFinish() # -T NUMBER
        self.opts.pmSetLongOptionDebug() # -D stuff
        self.opts.pmSetLongOptionHost() # -h HOST
        self.opts.pmSetLongOptionLocalPMDA() # -L
        self.opts.pmSetLongOptionInterval() # -t NUMBER
        self.opts.pmSetLongOption("elasticsearch-host", 1, 'g', '',
                                  "elasticsearch server host " +
                                  "(default \"http://localhost:80/\")")
        self.opts.pmSetLongOption("units", 1, 'u', '',
                                  "rescale all metric units " +
                                  "(e.g., \"mbytes/5 sec\")")
        self.opts.pmSetLongOption("index", 1, 'm', '',
                                  "elasticsearch index for metric names (default \"pcp\")")
        self.opts.pmSetLongOption("hostid", 1, 'i', '',
                                  "elasticsearch host-id for measurements (default from pmcd.hostname)")
        self.opts.pmSetLongOptionHelp()

        self.debug = False
        self.context = None
        self.hostid = None
        self.sample_count = 0
        self.elasticsearch_host = "http://localhost:9200/"
        self.es_index = "pcp"
        self.unitsstr = None
        self.units = None # pass verbatim by default
        self.units_mult = None # pass verbatim by default

        # now actually parse
        self.context = pmapi.pmContext.fromOptions(self.opts, sys.argv)

        if self.hostid is None:
            self.hostid = self.context.pmGetContextHostName()

        self.interval = self.opts.pmGetOptionInterval() or pmapi.timeval(60, 0)
        if self.unitsstr is not None:
            units = self.context.pmParseUnitsStr(self.unitsstr)
            (self.units, self.units_mult) = units
        self.metrics = []
        self.pmids = []
        self.descs = []
        metrics = self.opts.pmNonOptionsFromList(sys.argv)

        if metrics:
            for m in metrics:
                try:
                    self.context.pmTraversePMNS(m, self.handle_candidate_metric)
                except pmapi.pmErr as error:
                    sys.stderr.write("Excluding metric %s (%s)\n" %
                                     (m, str(error)))
            sys.stderr.flush()

        if len(self.metrics) == 0:
            sys.stderr.write("No acceptable metrics specified.\n")
            raise pmapi.pmUsageErr()

        # Create elasticsearch "index" (group-of-documents); no mapping
        self.es = Elasticsearch(hosts=[self.elasticsearch_host])
        self.es.indices.create(index=self.es_index,
                               ignore=[400],
                               body={'mappings':{'pcp-metric':
                                                 {'properties':{'@timestamp':{'type':'date'},
                                                                'host-id':{'type':'string'}}}}})

        # Report what we're about to do
        print("Relaying %d %smetric(s) with es_index %s from %s "
              "to %s every %f s" %
              (len(self.metrics),
               "rescaled " if self.units else "",
               self.es_index,
               self.describe_source(),
               self.elasticsearch_host,
               self.interval))
        sys.stdout.flush()

    def option_override(self, opt):
        if (opt == 'p') or (opt == 'g'):
            return 1
        if (opt == 'D'): # prior to pcp 3.10.8
            self.debug = True
            # fallthrough
        return 0

    def option(self, opt, optarg, index):
        # need only handle the non-common options
        if opt == 'g':
            self.elasticsearch_host = optarg
        elif opt == 'u':
            self.unitsstr = optarg
        elif opt == 'm':
            self.es_index = optarg
        elif opt == 'i':
            self.hostid = optarg
        else:
            raise pmapi.pmUsageErr()


    # Check the given metric name (a leaf in the PMNS) for
    # acceptability for elasticsearch: it needs to be numeric, and
    # convertable to the given unit (if specified).
    #
    # Print an error message here if needed; can't throw an exception
    # through the pmapi pmTraversePMNS wrapper.
    def handle_candidate_metric(self, name):
        try:
            pmid = self.context.pmLookupName(name)[0]
            desc = self.context.pmLookupDescs(pmid)[0]
        except pmapi.pmErr as err:
            sys.stderr.write("Excluding metric %s (%s)\n" % (name, str(err)))
            return

        # reject non-numeric types (future pmExtractValue failure)
        types = desc.contents.type
        if not ((types == c_api.PM_TYPE_32) or
                (types == c_api.PM_TYPE_U32) or
                (types == c_api.PM_TYPE_64) or
                (types == c_api.PM_TYPE_U64) or
                (types == c_api.PM_TYPE_FLOAT) or
                (types == c_api.PM_TYPE_DOUBLE) or
                (types == c_api.PM_TYPE_STRING)):
            sys.stderr.write("Excluding metric %s (%s)\n" %
                             (name, "need string or numeric type"))
            return

        # reject dimensionally incompatible (future pmConvScale failure)
        if self.units is not None:
            units = desc.contents.units
            if (units.dimSpace != self.units.dimSpace or
                units.dimTime != self.units.dimTime or
                units.dimCount != self.units.dimCount):
                      sys.stderr.write("Excluding metric %s (%s)\n" %
                                 (name, "incompatible dimensions"))
                      return

        self.metrics.append(name)
        self.pmids.append(pmid)
        self.descs.append(desc)


    # Convert a python list of pmids (numbers) to a ctypes LP_c_uint
    # (a C array of uints).
    def convert_pmids_to_ctypes(self, pmids):
        from ctypes import c_uint
        pmidA = (c_uint * len(pmids))()
        for i, p in enumerate(pmids):
            pmidA[i] = c_uint(p)
        return pmidA

    def execute(self):
        """ Using a PMAPI context (could be either host or archive),
            fetch and report a fixed set of values related to elasticsearch.
        """

        # align poll interval to host clock
        ctype = self.context.type
        if ctype == c_api.PM_CONTEXT_HOST or ctype == c_api.PM_CONTEXT_LOCAL:
            align = float(self.interval) - (time.time() % float(self.interval))
            time.sleep(align)

        # We would like to do: result = self.context.pmFetch(self.pmids)
        # But pmFetch takes ctypes array-of-uint's and not a python list;
        # ideally, pmFetch would become polymorphic to improve this code.
        result = self.context.pmFetch(self.convert_pmids_to_ctypes(self.pmids))
        sample_time_ms = result.contents.timestamp.tv_sec*1000.0 + result.contents.timestamp.tv_usec/1000.0

        if ctype == c_api.PM_CONTEXT_ARCHIVE:
            endtime = self.opts.pmGetOptionFinish()
            if endtime is not None:
                if float(sample_time_ms/1000.0) > float(endtime.tv_sec):
                    raise SystemExit

        # assemble all metrics into a single document
        # use @-prefixed keys for metadata not coming in from pcp metrics
        es_doc = {'@host-id': self.hostid,
                  '@timestamp': long(sample_time_ms)}

        for i, name in enumerate(self.metrics):
            for j in range(0, result.contents.get_numval(i)):
                # a fetch or other error will just omit that data value
                # from the elasticsearch-bound set
                try:
                    inst = result.contents.get_vlist(i,j).inst

                    if inst is None or inst < 0:
                        inst_name = None
                    else:
                        inst_name = self.context.pmNameInDom(self.descs[i], inst)

                    if (self.descs[i].contents.type == c_api.PM_TYPE_STRING):
                        atom = self.context.pmExtractValue(
                            result.contents.get_valfmt(i),
                            result.contents.get_vlist(i, j),
                            self.descs[i].contents.type, c_api.PM_TYPE_STRING)
                        value = atom.cp
                    else:
                        atom = self.context.pmExtractValue(
                            result.contents.get_valfmt(i),
                            result.contents.get_vlist(i, j),
                            self.descs[i].contents.type, c_api.PM_TYPE_DOUBLE)
                        # Rescale if desired
                        if self.units is not None:
                            atom = self.context.pmConvScale(c_api.PM_TYPE_DOUBLE,
                                                            atom,
                                                            self.descs, i,
                                                            self.units)
                            if self.units_mult is not None:
                                atom.d = atom.d * self.units_mult
                        value = atom.d

                    # install value into outgoing json/dict in key1{key2{key3=value}} style,
                    # namely:
                    # foo.bar.baz=value    =>  foo: { bar: { baz: value ...} }
                    # foo.bar.noo[0]=value =>  foo: { bar: { @instances:[{@id: 0, noo: value ...} ... ]}
                    pmns_parts=name.split(".")
                    pmns_leaf_dict = es_doc

                    # find/create the parent dictionary into which to insert the final component
                    for pmns_part in pmns_parts[:-1]:
                        if not(pmns_part in pmns_leaf_dict):
                            pmns_leaf_dict[pmns_part] = {}
                        pmns_leaf_dict = pmns_leaf_dict[pmns_part]
                    last_part = pmns_parts[-1]

                    if inst_name is None: # easy
                        pmns_leaf_dict[last_part] = value
                    else: # hard
                        iarray_name = "@instances"
                        if not (iarray_name in pmns_leaf_dict):
                            pmns_leaf_dict[iarray_name] = []
                        iarray = pmns_leaf_dict[iarray_name]
                        # find a preexisting {@id: inst_name} object in there, if any
                        iid_name = "@id"
                        foundit = False
                        for k in range(1,len(iarray)):
                            if iarray[k][iid_name] == inst_name:
                                iarray[k][last_part] = value
                                foundit = True
                        if not foundit:
                            iarray.append({iid_name:inst_name, last_part: value})

                except pmapi.pmErr as error:
                    sys.stderr.write("%s[%d]: %s, continuing\n" %
                                     (name, inst, str(error)))

        self.context.pmFreeResult(result)

        if self.debug:
            print (json.dumps(es_doc))

        self.es.index(index=self.es_index,
                      doc_type='pcp-metric',
                      timestamp=long(sample_time_ms), # XXX: ignored on some python-elasticsearch versions
                      body=es_doc)

        self.sample_count += 1
        max_samples = self.opts.pmGetOptionSamples()
        if max_samples is not None and self.sample_count >= max_samples:
            raise SystemExit


if __name__ == '__main__':
    try:
        G = ElasticsearchRelay()
        while True:
            G.execute()

    except pmapi.pmUsageErr as usage:
        sys.stderr.write("\n")
        usage.message()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        if str(error).find("PM_ERR_EOL") == -1:
            import traceback
            sys.stderr.write(str(error) + "\n") # init error: stop now
            sys.stderr.write(traceback.format_exc() + "\n")
