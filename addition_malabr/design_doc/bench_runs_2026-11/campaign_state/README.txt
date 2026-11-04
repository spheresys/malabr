OLD tree, if /tmp is lost: git archive 610c233 addition_malabr/mserver addition_malabr/scripts into a directory,
symlink addition_malabr/models to the real models, then overlay from 83e1ae9: mserver/tests/*.py and
mserver/malabr_service/calibration.py. Copy calibrations/ into its mserver/tests/ so W3 reuses them.
