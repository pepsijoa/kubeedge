#!/bin/bash
echo "Starting CloudCore..."
nohup sudo cloudcore > /home/kkw/kubeEdge/cloudcore.log 2>&1 &
echo "CloudCore started. Check logs at /home/kkw/kubeEdge/cloudcore.log"
