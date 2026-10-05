# AI Learning Companion

Lokales MVP eines universitären Studienprojekts: Der AI Learning Companion macht eigene PDF-Unterlagen als persönliche Wissensbasis nutzbar. Er beantwortet Lernfragen mit Quellen, erstellt Übungen und Probeklausuren und speichert Chats sowie generierte Inhalte für spätere Sitzungen.

## Architektur

```mermaid
flowchart LR
    D[Data Layer: Original-PDFs] --> K[Knowledge Base Layer: Notes + ChromaDB]
    K --> C[Connection Agent: Konzept- und Beziehungsvorschläge]
    C --> G[Persistenter Wissensatlas]
    G --> O[Optionaler Obsidian-Export]
    K --> A[Agentic Layer: Lernchat, Exercise, Exam und Critic]
```

- **Data Layer** (`src/data_layer`, `user_data`): PDFs speichern, SHA-256-Duplikate erkennen und Text mit PyMuPDF lesen.
- **Knowledge Base Layer** (`src/knowledge_layer`, `knowledge_base`): Inhalte thematisch extrahieren und als Markdown-Notes mit YAML-Metadaten speichern und semantisch durchsuchen.
- **Agentic Reasoning / Analytics Layer** (`src/agent_layer`): Lernchat, Exercise, Exam, Critic und Connection Agent greifen über Retrieval beziehungsweise belegte Notes und `LLMService` auf Wissen und Modelle zu.

SQLite (`src/persistence`) speichert Chats, Dokumente, Notes-Übersicht sowie den Second-Brain-Graph mit KI-generierten und nutzerbestätigten Beziehungen und die Review-Historie. `src/llm` kapselt beide Anbieter hinter `LLMService`. Markdown bleibt die Wissensrepräsentation der Notes; der kuratierte Graph wird zusätzlich als Obsidian-kompatible Markdown-Dateien exportiert.

## Voraussetzungen, Installation und Start

Benötigt werden Windows, Python 3.10 oder neuer mit pip (empfohlen: Python 3.12) und ein API-Key für OpenAI oder Google Gemini. Das Repository über GitHub herunterladen oder mit `git clone --branch Main https://github.com/LucaSp260/Studienprojekt_Steffen_Rams_Luca_Splittgerber.git` klonen. Anschließend unter Windows zuerst `setup.bat`, danach `start.bat` doppelt anklicken. Das Setup prüft Python, erstellt `.venv`, installiert alle Abhängigkeiten und initialisiert die lokalen Ordner sowie SQLite. Bereits eingerichtete Umgebungen lassen sich durch erneutes Ausführen von `setup.bat` aktualisieren. Falls der Browser nicht öffnet: http://localhost:8501. Zum Beenden im Terminal Strg+C drücken.

Ein eigener Python-Pfad kann mit `setup.bat "C:\Pfad\zu\python.exe"` gewählt werden. Die virtuelle Umgebung `.venv` wird durch `setup.bat` auf dem jeweiligen Rechner neu angelegt und ist nicht Teil des Downloads.

Der GitHub-Download enthält den Programmcode, aber keine API-Schlüssel, persönlichen PDFs, gespeicherten Chats oder fertige Wissensbasis. Nach dem ersten Start werden ein eigener API-Schlüssel eingerichtet und eigene PDF-Unterlagen hinzugefügt.

## Ein PDF in die Wissensbasis übernehmen

1. Unter **Einstellungen** OpenAI oder Google Gemini wählen, API-Key und Modellname angeben. **Speichern** übernimmt die Auswahl. Der Verbindungsstatus und **Verbindung testen** prüfen ausschließlich Modellmetadaten, ohne Textgenerierung.
2. Unter **Unterlagen** einen bestehenden Kurs auswählen oder **+ Neuen Kurs anlegen** wählen, einen Namen eingeben, PDFs auswählen und **Unterlagen hinzufügen** klicken.
3. **Unterlagen hinzufügen** speichert neue PDFs und startet direkt ihre Verarbeitung. Neue Notes werden automatisch eingebettet und indexiert; anschließend wächst der Wissensatlas inkrementell. Doppelte Uploads starten keine erneute Verarbeitung. Bei einem Fehler bleibt das Dokument gespeichert und kann über **Verarbeitung fortsetzen** weiterverarbeitet werden.
4. Unter **Generierte Notes** eine Note öffnen. **Generierte Notes** bietet einen Kursfilter (standardmäßig **Alle Kurse**), Inhalt, Tags, Schwierigkeit, Originaldatei und Quellseiten.

**Meine Unterlagen** zeigt PDFs nach Kurs gruppiert. Über **PDF löschen** und die anschließende Bestätigung werden das PDF, seine Knowledge Notes und deren Chroma-Einträge entfernt. Im Wissensatlas bleiben gemeinsam belegte Konzepte und Beziehungen mit ihren übrigen Belegen erhalten; nicht mehr belegte Elemente verschwinden. Ein vorhandener Obsidian-Export des Kurses wird aktualisiert. Frühere Chats und Lerninhalte bleiben lesbar und kennzeichnen Quellen aus gelöschten PDFs als nicht mehr verfügbar. Das Löschen ruft weder LLM noch Embedding-Provider auf.

Bei der Verarbeitung werden der extrahierte PDF-Text und gerenderte Bilder der PDF-Seiten an den gewählten Anbieter gesendet. API-Aufrufe können Kosten verursachen. Generative und Embedding-Aufrufe erfolgen bei ausdrücklich ausgelöster Dokumentverarbeitung, Lernfragen oder Generierung. Nur Einstellungen prüfen zusätzlich Modellmetadaten; Ergebnisse sind für fünf Minuten pro Provider, Modell und Schlüsselidentität gecacht. Das Öffnen alter Chats und Notes löst keine generative Anfrage aus. Bereits verarbeitete PDFs werden nicht erneut verarbeitet; Reprocessing ist nicht enthalten.

## Second Brain und Wissensatlas

1. Verarbeite zuerst PDF-Unterlagen zu Knowledge Notes.
2. Nach erfolgreicher Indexierung erweitert die App den Atlas automatisch: Neue Notes werden miteinander und mit höchstens fünf semantisch relevanten alten Notes je neuer Note verglichen. Insgesamt gelangen höchstens zwölf alte Notes in den Connection-Agent-Kontext. Bestehende Konzepte und Beziehungen werden nicht neu erzeugt. Dieser Schritt kann zusätzliche API-Kosten verursachen.
3. Öffne **Wissensatlas**. Die Gesamtansicht zeigt alle Konzepte und Beziehungen ohne Kantenlabels; ein Schalter blendet sie ein. Die Fokusansicht zeigt ein auswählbares Konzept und seine Nachbarn über einen oder zwei Hops mit direkt sichtbaren Beziehungskategorien. Hover/Klick zeigt Begründung, Belege, Herkunft und Review-Status.
4. Automatisch ergänzte Beziehungen sind **KI-generiert** und sofort sichtbar. Bestehende übernommene und manuelle Beziehungen bleiben **Nutzerbestätigt**. Beziehungen lassen sich später bearbeiten oder löschen; das Speichern einer Bearbeitung bestätigt die betreffende KI-Kante. Die normale Oberfläche bietet keine Vollkurs-Analyse und keinen zusätzlichen Connection-Agent-Button. Technische Vorschlags- und Review-Funktionen bleiben intern erhalten.
5. Unter **Optionaler Obsidian-Export → Obsidian-Export aktualisieren** entsteht ein kursbezogener Vault unter `knowledge_base/Second Brain/`. Exportierte Beziehungen tragen ihren Status im Markdown. Öffne den angezeigten Ordner in Obsidian als Vault und nutze **Graph View**.

Obsidian ist eine Visualisierung und externe Lesekopie. Die App bleibt die führende Wissensbasis. Offene und abgelehnte Vorschläge werden nicht als Verbindungen exportiert. Änderungen in Obsidian werden nicht in die App zurückimportiert.

Der Streamlit-Atlas startet in der **Gesamtansicht**. Die **Fokusansicht** bietet **Graph-Fokus** für ein Konzept und seine direkten Nachbarn (optional zwei Hops). Ein Knotenklick wählt das Konzept und zeigt kompakte Details und Bearbeitungsaktionen unter dem Graphen. Mausrad und Schaltflächen zoomen; Ziehen verschiebt den Graphen oder einzelne Knoten. Hover und Klick zeigen Quellen, Beschreibung und Begründung. **Beleg-Notes im Graphen anzeigen** ergänzt orange Note-Knoten zu den blauen Konzepten. Frühere Beziehungen behalten ihren ursprünglichen Typ; ihre bisherige Begründung steht zusätzlich als Beschreibung bereit, bis sie fachlich präzisiert werden.

Konzepte lassen sich lokal umbenennen und beschreiben; bestätigtes Löschen entfernt nur das Konzept und seine Graph-Kanten. Beziehungen lassen sich ansehen, bearbeiten und nach Bestätigung löschen. Knowledge Notes, Chroma und PDFs bleiben davon unberührt. Beschreibungen werden während des ohnehin stattfindenden Connection-Agent-Aufrufs erstellt; für ältere Konzepte verwendet die UI eine kurze lokale Zusammenfassung vorhandener Informationen. Knotenklicks benötigen keine API. Der frühere Prüfverlauf und manuelle Ergänzungsbereiche werden in der normalen UI nicht angezeigt; bestehende Historieneinträge bleiben gespeichert.

## Anbieter und lokale Einstellungen

Die Standardmodelle sind zentral in `src/llm/config.py` hinterlegt: OpenAI `gpt-5.6-luna` und Gemini `gemini-3.8-flash`. Der Modellname kann in der UI geändert werden; das gewählte Modell muss strukturierte Ausgaben unterstützen.

Konfiguration und Schlüssel liegen lokal in `.env`, nicht in SQLite. `.env` ist eine lokale Klartextdatei und von Git ausgeschlossen; sie sollte privat bleiben. Bereits gespeicherte Schlüssel werden nicht in das UI-Feld zurückgeladen. Ein leeres Feld behält den vorhandenen Schlüssel. Alternativ werden `OPENAI_API_KEY` beziehungsweise `GEMINI_API_KEY` aus der Prozessumgebung gelesen. `.env.example` dokumentiert alle Variablen. Eine Modelländerung oder ein Verbindungstest allein speichert noch keine Einstellungen.

Die Implementierung folgt den offiziellen SDKs und Dokumentationen: [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [OpenAI-Modell](https://developers.openai.com/api/docs/models/gpt-5.6-luna) und [Gemini Structured Outputs](https://ai.google.dev/gemini-api/docs/structured-output). OpenAI verwendet die Responses API, Gemini die Interactions API. Beide liefern schemaabhängiges JSON; Pydantic validiert es nochmals lokal.

## Verarbeitung und Persistenz

- Originale bleiben unter `user_data/<kurs>/`. Identischer Inhalt wird kursübergreifend nur einmal gespeichert, auch unter anderem Dateinamen. Gleichnamige Dateien mit anderem Inhalt erhalten einen nummerierten Namen.
- Chunking gruppiert maximal sechs Seiten und 16.000 Zeichen Quelltext pro Abschnitt. Überlange Seiten werden weiter geteilt und behalten ihre Seitennummer. Seitenangaben beziehen sich auf die physische PDF-Seite (beginnend bei 1), nicht auf eventuell abweichende aufgedruckte Seitenzahlen.
- Das Modell erzeugt thematische Notes statt einer Note pro Seite. Bereits erkannte Titel werden für einheitliche Bezeichnungen mitgegeben. Gleiche normalisierte Titel werden lokal zusammengeführt; Quellen und Inhalte bleiben dabei erhalten. Eine vollständige semantische Erkennung aller Synonyme ist nicht enthalten.
- Python prüft Pflichtfelder, Schwierigkeit, Tags und Seitenzahlen. Quellseiten müssen im jeweiligen Textabschnitt liegen. Kurs und Originaldateiname werden aus SQLite übernommen. Fachliche Richtigkeit und die genaue inhaltliche Zuordnung sollten anhand der angegebenen Quelle geprüft werden.
- Notes liegen in `knowledge_base/<kurs>/*.md`, mit Titel, Kurs, Thema, Tags, Schwierigkeit, Quelle, Seiten und verwandten Themen im YAML-Frontmatter. SQLite registriert sie in `knowledge_notes` mit Dokumentbezug und relativem Markdown-Pfad.
- Für neue Dokumente wird `documents.processed = 1` erst nach Markdown, SQLite und erfolgreicher Indexierung gesetzt. Schreibfehler vor dem SQLite-Commit werden zurückgenommen. Bei späteren Embedding-/Chroma-Fehlern bleiben die Notes erhalten, das Dokument bleibt unverarbeitet. Der nächste Versuch indexiert die vorhandenen Notes ohne erneute Knowledge Extraction. Jede physische PDF-Seite wird lokal als Bild gerendert und zusammen mit dem Text an den ausgewählten multimodalen Anbieter übermittelt; dadurch können auch Scans, Abbildungen, Diagramme und handschriftliche Ergänzungen in derselben Knowledge Extraction berücksichtigt werden.
- `brain_note_processing` merkt sich abgeschlossene Connection-Analysen. Bei der einmaligen Migration werden vorhandene Notes als Bestand markiert, damit sie nicht nachträglich kostenpflichtig analysiert werden. Scheitert nur der Connection-Schritt, bleiben die indexierten Notes erhalten und er kann unter **Unterlagen** ohne neue Extraction erneut gestartet werden. `brain_edges.review_status` unterscheidet KI-generierte von nutzerbestätigten Beziehungen; `llm_usage` speichert Anbieter, Modell, Operation, Zeitpunkt und nur tatsächlich gelieferte Tokenwerte.

Datenbank und fehlende Tabellen werden beim Start automatisch angelegt, bestehende Daten bleiben erhalten. PDFs, Chats und Notes überstehen normale Neustarts. Die Anwendung ist für eine lokale Streamlit-Instanz vorgesehen; eine Verarbeitungssperre schützt vor gleichzeitigen Klicks in deren Browsersitzungen. Ein harter Prozessabbruch während des Schreibens kann unregistrierte Markdown-Dateien zurücklassen; bestehende Dateien werden auch dann nicht überschrieben. Zeitstempel in SQLite sind UTC.

Beim Neustart wird der zuletzt aktualisierte Chat samt gespeicherter Antworten und Quellen geladen; ältere Chats bleiben auswählbar.

## Lernchat

Unter **Chat** lassen sich Fragen zur persönlichen Wissensbasis stellen. Die direkt erreichbaren Sidebar-Bereiche **Meine Inhalte**, **Übung erstellen** und **Probeklausur erstellen** führen ohne zusätzlichen Moduswechsel zu den gespeicherten Artefakten beziehungsweise Erstellungsformularen. Optional begrenzt ein Kursfilter die Suche. Der Chat ruft passende Notes über dasselbe semantische Retrieval wie die Wissensbasis-Suche ab, gibt dem Modell nur diese Inhalte und zeigt an jeder Antwort die tatsächlich verwendeten PDF-Dateien und Seiten. Eine Antwort ohne belegte Quelle wird nicht als wissensbasierte Antwort ausgegeben. Wenn die Wissensbasis nicht genügend Inhalt liefert, meldet der Chat das offen.

Kurze Anschlussfragen berücksichtigen höchstens die sechs letzten Nachrichten als Gesprächskontext. Dieser Kontext hilft bei der Auflösung von Bezügen; die Antwort muss weiterhin durch neu abgerufene Notes gedeckt sein. Frage, Antwort, Kursfilter und Quellen werden gemeinsam in SQLite gespeichert. Alte Chats können deshalb nach einem Neustart ohne LLM- oder Embedding-Aufruf gelesen werden.

## Übungen, Probeklausuren und Critic Agent

Erstellte mathematische Formeln werden als LaTeX mit Inline- oder abgesetzten Delimitern ausgegeben und durch Streamlit gerendert. Vorhandene Chatantworten mit `\(...\)`, `\[...\]` oder einer alleinstehenden eckigen Formelschreibweise werden bei der Anzeige für Streamlit konvertiert.

In **Übung erstellen** und **Probeklausur erstellen** lässt sich **Critic Agent zur Qualitätsprüfung verwenden (zusätzlicher KI-Aufruf)** jeweils ein- oder ausschalten. Standardmäßig ist die Prüfung aktiviert. Ohne Critic entfällt dessen zusätzlicher Modellaufruf; die lokale Struktur- und Quellenvalidierung bleibt erhalten. Diese Inhalte werden als ohne Critic erstellt gekennzeichnet.

Der Ablauf ist bewusst klein und nachvollziehbar:

```text
Mit Critic:  Retrieval → Generator → Critic (ggf. überarbeitete Fassung) → Final
Ohne Critic: Retrieval → Generator → lokale Struktur-/Quellenprüfung → Final
```

- Der **Exercise Agent** bildet aus Kurs, Thema, Anzahl, Schwierigkeit und Aufgabentyp eine Retrieval-Anfrage. Er verwendet standardmäßig höchstens fünf Notes und erzeugt Aufgaben mit Musterlösung, Erklärung und Quellen.
- Der **Exam Agent** erstellt aus mehreren semantisch gefundenen Notes eine Probeklausur mit Aufgaben, Punkten und Lösungen. Die neue Oberfläche benötigt keine Minutendauer; Generator und Critic optimieren keine Aufgaben an Zeitvorgaben. Alte gespeicherte Klausuren mit Dauer bleiben lesbar. Die interne optionale Unterstützung expliziter historischer Duration-Konfigurationen bleibt für Rückwärtskompatibilität erhalten.
- Der **Critic Agent** prüft Grounding, Lösung, Klarheit, Schwierigkeit, Redundanz und Quellen; bei neuen Klausuren zusätzlich Vielfalt und Punkte. Er darf höchstens eine vollständig überarbeitete Fassung liefern; es gibt keine Agentenschleife. Schlägt die Prüfung fehl, wird der Entwurf sichtbar als ungeprüft markiert.

Aufgaben besitzen optional strukturierte Teilaufgaben. Jede Teilaufgabe, Kurzantwort und Erklärung trägt dieselbe ID, zum Beispiel `a`, `b`, `c` oder `1`, `2`, `3`. Pydantic verwirft Ergebnisse, wenn IDs oder Reihenfolge zwischen `subtasks`, `short_answer_items` und `explanation_items` abweichen. Die UI zeigt jedes Element in einem eigenen Absatz. Bei Aufgaben ohne Teilaufgaben bleiben `solution` und `explanation` die beiden Lösungsebenen. Alte Artefakte ohne die neuen Listen bleiben über diese bisherigen Felder lesbar.

Vor dem Modellaufruf erhalten Retrieval-Treffer lokale IDs wie `SOURCE_1`. Das Modell darf nur diese IDs verwenden. Python validiert jede ID und übersetzt sie für die Anzeige in den tatsächlichen PDF-Dateinamen und die Seiten der gefundenen Knowledge Note. Ungültige oder erfundene IDs führen zu einem Fehler.

Das ist mehr als ein einzelner LLM-Prompt: Die Inhalte stammen aus der persistenten persönlichen Knowledge Base, werden gezielt semantisch abgerufen, auf diese Quellen begrenzt, strukturiert validiert und bei aktiviertem Critic anschließend durch einen spezialisierten zweiten Agenten geprüft. Fertige Übungen und Klausuren liegen als JSON in SQLite in `generated_artifacts` und können unter **Meine Inhalte** nach einem Neustart ohne API-Aufruf wieder geöffnet werden.

## Semantisches Retrieval und Embeddings

Neue Notes werden automatisch indexiert. Der manuelle Indexierungsbutton ist aus der Benutzeroberfläche entfernt; `index_existing_notes` bleibt intern für gezielte Reparaturen und Migrationen vorhanden und wird beim Start nicht automatisch auf den Bestand angewendet.

Standards in `src/llm/config.py`: OpenAI `text-embedding-3-small`, Gemini `gemini-embedding-2`, jeweils 768 Dimensionen. Bereits gespeicherte kompatible Embedding-Modelle aus `.env` werden weiterverwendet. Die normale Einstellungsseite bietet kein Embedding-Modellfeld und speichert diese interne Konfiguration unverändert.

**Bewusst kein Graph-RAG:** Lernchat, Übungen und Probeklausuren verwenden ausschließlich die vorhandene semantische Vorauswahl von Knowledge Notes. Es werden weder Graphnachbarn noch zusätzliche Notes aus dem Wissensatlas in den Generierungskontext aufgenommen. Der Graph dient Strukturierung, Exploration, Visualisierung und Quellen-Navigation. Das hält Retrieval und Tokenverbrauch begrenzt.

Der Suchtext enthält Titel, Thema, Tags und den fachlichen Markdown-Inhalt, keine technischen YAML-Felder. Sehr lange Notes werden in Abschnitte von höchstens 6.000 UTF-8-Bytes eingebettet; deren längengewichtetes Mittel ergibt einen normalisierten Vektor je Note. Es wird kein Inhalt still abgeschnitten.

**SQLite** verwaltet Chats, Dokumentstatus und Note-Zuordnung. **Markdown** bleibt die vollständige Wissensrepräsentation. **ChromaDB** speichert nur Suchvektoren und Metadaten lokal unter `chroma_db/`, ohne externen Server oder Cloud-Account. ChromaDB ist Infrastruktur des Knowledge Base Layers, kein zusätzlicher fachlicher Layer. Der Ordner bleibt von Git ausgeschlossen.

Jede Kombination aus Provider, Embedding-Modell, Dimension und Indexformat erhält eine eigene Collection. Stabile IDs wie `knowledge_note_7` und Upserts verhindern Duplikate. Eine bewusst technisch durchgeführte Embedding-Modellmigration kann intern `index_existing_notes` verwenden; der frühere Index bleibt erhalten. Der Dokumentstatus dokumentiert eine abgeschlossene Verarbeitung; die Suchabdeckung ist vom aktuell gewählten Modell abhängig. App-Start und Streamlit-Neuläufe lösen keine Embedding-Anfragen aus.

Technische Quellen: [OpenAI Embeddings](https://developers.openai.com/api/docs/guides/embeddings), [Gemini Embeddings](https://ai.google.dev/gemini-api/docs/embeddings), [Chroma Collections](https://docs.trychroma.com/docs/collections/manage-collections).

## Datenschutz und lokale Daten

PDFs, Markdown-Notes, SQLite-Daten, Chroma-Vektoren und API-Konfiguration liegen lokal in den Ordnern `user_data/`, `knowledge_base/`, `chroma_db/`, `data/` beziehungsweise in `.env`. Diese Pfade und Python-Umgebungen sind in `.gitignore` ausgeschlossen. API-Keys werden nicht in SQLite oder in generierten Inhalten gespeichert.

Das Projekt arbeitet nicht vollständig offline: Für Extraktion, Embeddings, Lernchat, Übungen und Klausuren werden die jeweils nötigen Inhalte an den gewählten Anbieter OpenAI oder Google Gemini übertragen. Bei jeder Knowledge Extraction werden neben dem Text auch gerenderte Bilder aller PDF-Seiten übertragen, einschließlich Seiten ohne Textlayer. Das kann den Tokenverbrauch und die Verarbeitungsdauer deutlich erhöhen. Welche Daten dabei verarbeitet werden, richtet sich zusätzlich nach den Bedingungen und Einstellungen des Anbieters. Vor einer Weitergabe des Projekts sollten `.env` und alle lokalen Datenordner privat bleiben.

## Warum nicht einfach ein PDF in ChatGPT laden?

Der Companion verwaltet mehrere Unterlagen dauerhaft als eigene, lokal nachvollziehbare Wissensbasis. Er erkennt bereits verarbeitete Dokumente, hält Quellen bis auf PDF-Seitenebene fest, sucht kursübergreifend oder mit Kursfilter und speichert Antworten zusammen mit den konkret verwendeten Quellen. Übungen und Klausuren verwenden dieselbe Wissensbasis, validieren ihre strukturierte Ausgabe und können bei aktiviertem Schalter einen getrennten Critic Agent genau eine Qualitätsprüfung durchführen lassen. Dadurch bleibt der Ablauf reproduzierbarer als ein einzelner, isolierter Datei-Chat.

## Bekannte Grenzen

- Bilder, Diagramme und Handschrift werden durch das multimodale Modell gemeinsam mit dem extrahierten PDF-Text ausgewertet. Unleserliche oder sehr kleine Inhalte können trotzdem übersehen oder falsch erkannt werden; handschriftliche Erkennung ist nicht garantiert.
- Da jede Seite als Bild mitgesendet wird, kann die Dokumentverarbeitung mehr Bildtokens und längere Laufzeiten verursachen als reine Textextraktion. Es gibt keinen zusätzlichen Vision-Agent-Aufruf: Bildanalyse ist Teil derselben Knowledge-Extraction-Anfrage pro Abschnitt.
- Antworten und generierte Aufgaben können trotz Quellenbindung fachliche Fehler enthalten. Quellen sollten bei wichtigen Aussagen geprüft werden.
- Die Seitennummer bezeichnet die physische PDF-Seite und kann von einer aufgedruckten Nummer abweichen.
- Knowledge Extraction, Embeddings und Modellantworten können API-Kosten verursachen.
- Gleichartige Themen werden anhand normalisierter Titel zusammengeführt; eine vollständige semantische Synonymerkennung ist nicht enthalten.
- Die Anwendung ist als lokales Studienprojekt für eine einzelne Person ausgelegt, nicht als Mehrbenutzersystem.

## Tests

Kostenfrei und ohne persönliche Unterlagen:

```text
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Die Suite erzeugt temporäre PDFs und Datenbanken. Sie prüft Upload, Duplikate, Chat-Persistenz einschließlich Quellen, begrenzten Anschlusskontext, Navigation ohne API-Aufruf, Migrationen, Chunking, Quellenvalidierung, JSON/YAML/Markdown, Fehler-Rollback, Konfiguration, Setup-Skripte und UI-Neustarts mit Mock-LLM. Suchtests verwenden echte temporäre Chroma-Dateien mit Mock-Embeddings. Agent-Tests mocken Retrieval und LLM und prüfen Anzahl, Schwierigkeit, Lösungen, Source-IDs, Vielfalt, Punkte, Dauer, eine einzelne Revision, Fehlerbehandlung und Artefakt-Persistenz.

Optionaler echter API-Test (kann Kosten verursachen):

```text
.venv\Scripts\python.exe tests/manual_api_check.py
```

Dieser Test nutzt ausschließlich eine selbst erzeugte kleine PDF, einen vorhandenen Schlüssel und temporäre Dateien. Ohne Schlüssel wird er übersprungen.

Ein expliziter echter Retrieval-Test für die vorhandenen Notes (OpenAI, kostenpflichtige API-Aufrufe) ist ebenfalls verfügbar:

```text
.venv\Scripts\python.exe tests/manual_retrieval_check.py
```

Er indexiert die vorhandenen Notes ohne Knowledge Extraction, prüft beide EAM-Testanfragen sowie einen neuen Prozess und kontrolliert, dass die Markdown-Dateien unverändert bleiben. Ergebnisse liegen in `chroma_db/retrieval_test_report.json`.

Die zwei kleinen echten Phase-5-Szenarien aus der Aufgabenbeschreibung lassen sich separat starten:

```text
.venv\Scripts\python.exe tests/manual_agent_check.py
```

Der Lauf verwendet die vorhandene SWA-Wissensbasis, erzeugt zwei Übungen und eine 30-minütige Probeklausur, speichert beide Artefakte und lädt sie in einem neuen Python-Prozess erneut. Er führt zwei Embedding-Anfragen sowie je einen Generator- und Critic-Aufruf pro Artefakt aus und kann API-Kosten verursachen.

Der kleine echte Phase-6-Check stellt eine Hauptfrage und eine Anschlussfrage an die vorhandene Wissensbasis, speichert beide Antworten mit Quellen und prüft sie nach einem Prozessneustart:

```text
.venv\Scripts\python.exe tests/manual_chat_check.py
```

Der Lauf verursacht je Frage genau einen Retrieval- und einen LLM-Aufruf. Vorhandene PDFs, Notes, Übungen und Klausuren werden nur gelesen und nicht erneut erzeugt.

## Lokale Verwaltung und Datenintegrität

Die Sidebar zeigt Navigation vor einer separat scrollbar gehaltenen Chatliste. **Chat hinzufügen** startet einen persistenten Chat. Pins überleben Neustarts; angepinnte Chats stehen zuerst, danach folgt die letzte Aktivität. Eine bestätigte Chat-Löschung entfernt zugehörige Nachrichten; unabhängige Lernartefakte, Notes und PDFs bleiben erhalten. Alte chatbezogene Klausuren verlieren nur ihre Chatzuordnung.

Die Kursauswahl liegt im nativen `st.bottom`-Bereich. Chat, **Meine Inhalte**, **Übung erstellen** und **Probeklausur erstellen** sind eigene Sidebar-Navigationen. **Meine Inhalte** nutzt denselben Kursfilter mit **Alle Kurse** als Standard. Das Löschen eines einzelnen Artefakts erfordert eine Bestätigung. Die Quellenanzeige zeigt weiterhin Original-PDF-Seiten; interne RAG-Kontextblöcke werden nicht angezeigt.

Neue Notes besitzen maximal fünf Tags mit jeweils höchstens drei Wörtern. Lokale Normalisierung vereinheitlicht Unicode, Groß-/Kleinschreibung und Trennzeichen, entfernt lange Tags und Duplikate und verwendet eindeutige vorhandene Tags desselben Kurses wieder. Die Tagliste wird nicht zusätzlich an das LLM geschickt. Alte Notes werden nicht rückwirkend bereinigt und es gibt keinen separaten Tag-LLM-Aufruf.

`courses` ergänzt SQLite minimal um bekannte Kursnamen. Neue Namen werden getrimmt und gegen Case-Duplikate geprüft. **Unterlagen → Kurs umbenennen** aktualisiert Dokumentzuordnungen, Markdown-Frontmatter, alle lokalen Chroma-Collections (nur Metadaten und Hash), Konzepte/Vorschläge, gespeicherte Inhaltskonfigurationen und Chat-Kursfilter. Stabile Dokument-/Note-IDs und Dateipfade bleiben erhalten. `brain_note_processing` und Usage-Daten bleiben erhalten. Bereits vorhandene Obsidian-Vaults werden lokal zur neuen Kurszuordnung verschoben; ein Export aktualisiert weiterhin den sichtbaren Stand.

Vor der Pin-Migration und vor jeder Kursumbenennung entsteht eine mit der SQLite-Backup-API erstellte Sicherung unter `data/*.before-*.db`. Kursumbenennung sperrt parallele lokale Verarbeitung/Indexierung, verwendet `BEGIN IMMEDIATE`, atomare Markdown-Ersetzung und eine Rücknahme von Dateien und Chroma-Metadaten bei Fehlern. SQLite, Dateisystem und Chroma unterstützen zusammen keine echte gemeinsame ACID-Transaktion: ein harter Prozessabbruch zwischen diesen Schritten bleibt ein Wiederherstellungsfall für die Sicherung. Zielkurse werden niemals automatisch zusammengeführt. Normale UI-Aktionen berechnen keine Embeddings neu.

Der Verbindungsstatus nutzt [OpenAI Modellmetadaten](https://developers.openai.com/api/reference/resources/models/methods/retrieve) beziehungsweise [Gemini Modellmetadaten](https://ai.google.dev/api/models). Eine erfolgreiche Abfrage bestätigt Erreichbarkeit und Modellzugriff, aber nicht verfügbares Guthaben oder jede Generierungsfunktion. Fehler werden ohne rohe Providerantworten angezeigt. Es findet keine generative Testanfrage statt.

PDF-Text wird weiterhin lokal mit PyMuPDF extrahiert. Zusätzlich rendert PyMuPDF jede Seite als PNG; das konfigurierte OpenAI- oder Gemini-Modell analysiert diese Bilder zusammen mit dem Text innerhalb der bestehenden Knowledge Extraction. Dadurch werden auch Scans ohne Textlayer, Diagramme und Handschrift berücksichtigt, ohne einen neuen Agenten oder eine separate Anfrage pro Seite einzuführen. Die Drei-Layer-Architektur bleibt erhalten.


Übungen können ohne **Thema oder Beschreibung** erstellt werden. Dann verwendet das
begrenzte semantische Retrieval zentrale Inhalte des ausgewählten Kurses; es gibt
keinen zusätzlichen KI-Aufruf zur Themenauswahl. **Meine Inhalte** gruppiert gespeicherte
Klausuren unter **Übungsklausuren** und einzelne Übungssets unter **Übungsaufgaben**.

Beim ausgewählten Atlas-Konzept werden die Original-PDF-Seiten seiner belegenden
Notes angeboten, nach Dokument zusammengefasst und ohne doppelte Seiten. Die Anzeige
verwendet dieselbe lokale PDF-Vorschau wie der Lernchat und löst keinen LLM-Aufruf aus.

Die deutsche Oberfläche setzt zentral lang="de" und translate="no" sowie den
Browser-Übersetzungsschutz. Dadurch sollen automatische Übersetzer keine von React
verwalteten Texte ersetzen (vertauschte Überschriften / removeChild-Fehler).
Wenn eine offene Seite bereits durch Browserübersetzung beschädigt wurde, die Seite
einmal vollständig neu laden und gegebenenfalls „Original anzeigen“ wählen.
