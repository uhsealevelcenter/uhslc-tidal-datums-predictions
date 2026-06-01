#!/bin/sh -f 

### ACTIVATE VIRTUAL AND PROCESS ENV'S. ###
. /home/nwstg/timescale/utils/initialize_process_env.sh
prepare_runtime_or_die /etc/environment

### CODE DIRECTORY. ###
CODE_DIR="/home/nwstg/uhslc-tidal-datums-predictions"

### CHANGE TO CODE DIRECTORY. ###
cd "$CODE_DIR" || exit 1
echo "running from: $(pwd)"

### UHSLC ID'S TO PROCESS. ###
UHSLC_IDS="002 003 007 014 026 027 057 421 422 423 424 425 426 428 429 430 431 432 435 436 437 438 547 548 553"

### LOOP THROUGH UHSLC IDS. ###
for UHSLC_ID in $UHSLC_IDS
do

  ### UPDATE EPOCH'S, TIDE PREDICTIONS, DATUMS, AND CONSTITUENTS. ###
  MPLCONFIGDIR=/tmp/mplconfig python ${CODE_DIR}/scripts/run_station_datums_predictions.py --station-id ${UHSLC_ID}

done
