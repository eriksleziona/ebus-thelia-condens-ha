#!/bin/bash
rrdtool create heating.rrd \
--step 60 \
DS:flow:GAUGE:120:0:90 \
DS:return:GAUGE:120:0:70 \
DS:delta:GAUGE:120:0:40 \
DS:burner:GAUGE:120:0:1 \
DS:modulation:GAUGE:120:0:100 \
RRA:AVERAGE:0.5:1:43200 \
RRA:AVERAGE:0.5:5:52560 \
RRA:AVERAGE:0.5:60:17520 \
RRA:AVERAGE:0.5:1440:1825
