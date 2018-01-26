#!/bin/bash
DATA_DIR="/var/lib/rabbitmq/data"
HOSTNAME=$(hostname -s)
DATA_FILE="${DATA_DIR}/${HOSTNAME}_cluster_state.dat"
TMP_FILE=$(mktemp -p ${DATA_DIR})

trap "rm -f $TMP_FILE > /dev/null 2>&1" EXIT

/usr/sbin/rabbitmqctl -q cluster_status >$TMP_FILE 2>/dev/null
if [ $? -eq 0 ]; then
    chown rabbitmq:rabbitmq ${TMP_FILE}
    chmod 0644 ${TMP_FILE}
    mv ${TMP_FILE} ${DATA_FILE}
else
    rm -f $TMP_FILE
fi

trap - EXIT
