#!/bin/sh -f 

### ACTIVATE VIRTUAL AND PROCESS ENV'S. ###
. /home/nwstg/timescale/utils/initialize_process_env.sh
prepare_runtime_or_die /etc/environment

### CODE DIRECTORY. ###
CODE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

### PRODUCTION ARTIFACT WEB/REVIEW DIRECTORY. ###
ARTIFACT_REVIEW_ROOT="/srv/htdocs/uhslc.soest.hawaii.edu/tech/datums_predictions_review"

### CHANGE TO CODE DIRECTORY. ###
cd "$CODE_DIR" || exit 1
echo "running from: $(pwd)"

### UHSLC ID'S TO PROCESS. ###
UHSLC_IDS="002 003 007 014 026 027 057 421 422 423 424 425 426 428 429 430 431 432 436 437 438 547 548 553"

### LOOP THROUGH UHSLC IDS. ###
FAILED=""
for UHSLC_ID in $UHSLC_IDS
do

  ### UPDATE EPOCHS, TIDE PREDICTIONS, DATUMS, AND CONSTITUENTS. ###
  if MPLCONFIGDIR=/tmp/mplconfig \
      python "$CODE_DIR/scripts/run_station_datums_predictions.py" \
      --station-id "$UHSLC_ID"
  then

    ### PUBLISH THIS STATION'S ARTIFACTS ON PROD ONLY. ###
    if [ "$PROCESS_ENV" = "prod" ]; then
      SOURCE_DIR="$CODE_DIR/artifacts/datums_predictions/station${UHSLC_ID}"
      DEST_DIR="$ARTIFACT_REVIEW_ROOT/station${UHSLC_ID}"

      if [ ! -d "$SOURCE_DIR" ]; then
        echo "FAILED station ${UHSLC_ID}: artifact directory not found: ${SOURCE_DIR}" >&2
        FAILED="${FAILED} ${UHSLC_ID}"
        continue
      fi

      mkdir -p "$DEST_DIR" || {
        echo "FAILED station ${UHSLC_ID}: could not create ${DEST_DIR}" >&2
        FAILED="${FAILED} ${UHSLC_ID}"
        continue
      }

      if ! rsync -a --delete "$SOURCE_DIR/" "$DEST_DIR/"; then
        echo "FAILED station ${UHSLC_ID}: artifact rsync failed" >&2
        FAILED="${FAILED} ${UHSLC_ID}"
        continue
      fi

      echo "Published station ${UHSLC_ID} artifacts to ${DEST_DIR}"
    fi

  else
    STATUS=$?
    echo "FAILED station ${UHSLC_ID} with exit code ${STATUS}" >&2
    FAILED="${FAILED} ${UHSLC_ID}"
  fi

done

if [ -n "$FAILED" ]; then
  echo "Batch completed with failed stations:${FAILED}" >&2
  exit 1
fi
