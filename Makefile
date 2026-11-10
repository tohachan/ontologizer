# Local Open Ontologies server. The logic lives in deps/open-ontologies/server.sh;
# config in deps/open-ontologies/defaults.conf and local.conf (gitignored).
# Any setting can be given per call: make oo-server OO_SRC=../open-ontologies

OO_SERVER := deps/open-ontologies/server.sh
VIZ := deps/visualizer/viz.sh

.DEFAULT_GOAL := help
.PHONY: help oo-status oo-server oo-clone oo-rebuild oo-down oo-logs viz-up viz-down viz-status viz-logs

help:
	@echo "make oo-status   config, clone, server and token check, container"
	@echo "make oo-server   start the server unless one already answers at OO_URL"
	@echo "                 (clones the engine and writes a token to .env if missing)"
	@echo "make oo-clone    clone the engine into OO_SRC if it is not there"
	@echo "make oo-rebuild  rebuild the image and recreate the container (data kept)"
	@echo "make oo-down     stop the server (data volume kept)"
	@echo "make oo-logs     last lines of the engine log"
	@echo "make viz-up      build and start the visualizer at http://127.0.0.1:8090/ (VIZ_PORT)"
	@echo "make viz-down    stop the visualizer (the engine is not touched)"
	@echo "make viz-status  the visualizer's container and whether it answers"
	@echo "make viz-logs    last lines of the visualizer log"

oo-status:
	@$(OO_SERVER) status

oo-server:
	@$(OO_SERVER) up

oo-clone:
	@$(OO_SERVER) clone

oo-rebuild:
	@$(OO_SERVER) rebuild

oo-down:
	@$(OO_SERVER) down

oo-logs:
	@$(OO_SERVER) logs

viz-up:
	@$(VIZ) up

viz-down:
	@$(VIZ) down

viz-status:
	@$(VIZ) status

viz-logs:
	@$(VIZ) logs
