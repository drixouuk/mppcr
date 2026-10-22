#!/usr/bin/env python3
"""Sonde : formats de décalage lus par MPXJ et convertis par lag_days.

    python3 sonde_lags.py test_lags.xml
"""
import sys

import montecarlo as mc

proj = mc.UniversalProjectReader().read(sys.argv[1])
mc.PROJECT_CALENDAR = proj.getProjectProperties().getDefaultCalendar()
for tache in proj.getTasks():
    for rel in tache.getPredecessors() or []:
        lag = rel.getLag()
        print(f"{tache.getName()} : brut={lag.getDuration()} unite={lag.getUnits()} "
              f"-> lag_days={mc.lag_days(rel):.3f}")
