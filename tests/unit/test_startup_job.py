"""Évènements LibreOffice qui exécutent le job de l'extension (oxt/Jobs.xcu).

Pour chaque évènement abonné, LibreOffice construit un `MainJob` neuf, et c'est
le constructeur qui fait le travail de démarrage : langue, User-Agent, trace de
lancement, état du Device Management, tâches de fond.
"""

import os
import xml.etree.ElementTree as ET

_JOBS_XCU = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "oxt", "Jobs.xcu"))
_OOR_NAME = "{http://openoffice.org/2001/registry}name"


def _named(parent, name):
    return [node for node in parent.findall("node") if node.get(_OOR_NAME) == name]


def _events_that_run(service):
    """Évènements dont la JobList nomme un job déclaré sous Jobs avec ce service,
    comme LibreOffice les résout."""
    root = ET.parse(_JOBS_XCU).getroot()
    jobs = {job.get(_OOR_NAME) for section in _named(root, "Jobs")
            for job in section.findall("node")
            if any(prop.get(_OOR_NAME) == "Service" and prop.findtext("value") == service
                   for prop in job.findall("prop"))}
    return sorted(event.get(_OOR_NAME) for section in _named(root, "Events")
                  for event in section.findall("node")
                  if any(entry.get(_OOR_NAME) in jobs
                         for job_list in _named(event, "JobList")
                         for entry in job_list.findall("node")))


def test_only_the_first_window_runs_the_extension_job():
    """Le job tourne quand LibreOffice affiche sa première fenêtre, jamais à
    l'ouverture ni à la création d'un document : chaque exécution reconstruirait
    MainJob et renverrait la trace de lancement."""
    assert _events_that_run("fr.gouv.interieur.mirai.do") == ["onFirstVisibleTask"]
