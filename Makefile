.PHONY: install run terminal debug test clean release release-env release-env-clean

RELEASE_VENV := .venv-release
RELEASE_PY := $(RELEASE_VENV)/bin/python
RELEASE_PIP := $(RELEASE_VENV)/bin/pip
MODEL_PATH := models/pose_landmarker_heavy.task
MODEL_URL := https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task

# One-command setup: venv + deps + model
install:
	@./install.sh

# Run the menubar app
run:
	@source venv/bin/activate && python menubar.py

# Run in terminal mode
terminal:
	@source venv/bin/activate && python main.py

# Run the visual debug overlay
debug:
	@source venv/bin/activate && python visualize.py

# Run tests
test:
	@source venv/bin/activate && python -m pytest tests/ -v

# Isolated environment used only for .app/.dmg packaging
release-env:
	@echo "=> Preparing isolated release environment..."
	@if [ ! -d "$(RELEASE_VENV)" ]; then python3 -m venv "$(RELEASE_VENV)"; fi
	@$(RELEASE_PIP) install -q --upgrade pip
	@$(RELEASE_PIP) install -q -r requirements-release.txt
	@if [ ! -f "$(MODEL_PATH)" ]; then \
		echo "=> Downloading pose model for release build..."; \
		mkdir -p models; \
		curl -sSL -o "$(MODEL_PATH)" "$(MODEL_URL)"; \
	fi

release-env-clean:
	rm -rf $(RELEASE_VENV)

# Clean build artifacts
clean:
	rm -rf build .eggs *.egg-info
	@if [ -d dist ]; then \
		chmod -R u+rw dist >/dev/null 2>&1 || true; \
		find dist -mindepth 1 -exec rm -rf {} + >/dev/null 2>&1 || true; \
	fi
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Package into a standalone Mac app and DMG
release: release-env clean
	@echo "=> Building Slouchy.app via py2app..."
	@$(RELEASE_PY) setup.py py2app
	@echo "=> Pruning unused bundle assets..."
	@find dist/Slouchy.app/Contents/Resources/lib -type d -path "*/cv2/data" -prune -exec rm -rf {} + >/dev/null 2>&1 || true
	@find dist/Slouchy.app/Contents/Resources/lib -type d \( -name tests -o -name test -o -name testing \) -prune -exec rm -rf {} + >/dev/null 2>&1 || true
	@rm -rf dist/Slouchy.app/Contents/Resources/lib/python3.14/setuptools \
		dist/Slouchy.app/Contents/Resources/lib/python3.14/pkg_resources \
		dist/Slouchy.app/Contents/Resources/lib/python3.14/setuptools-*.dist-info \
		dist/Slouchy.app/Contents/Resources/lib/python3.14/wheel-*.dist-info >/dev/null 2>&1 || true
	@echo "=> Preparing DMG workspace..."
	@chmod -R u+rw dist/Slouchy.app || true
	@rm -rf dist/dmg-root
	@mkdir -p dist/dmg-root
	@cp -R dist/Slouchy.app dist/dmg-root/
	@for vol in /Volumes/Slouchy*; do \
		if [ -d "$$vol" ]; then \
			echo "=> Detaching stale volume $$vol"; \
			hdiutil detach "$$vol" >/dev/null 2>&1 || hdiutil detach -force "$$vol" >/dev/null 2>&1 || true; \
		fi; \
	done
	@echo "=> Creating Slouchy.dmg..."
	@rm -f dist/Slouchy.dmg dist/Slouchy-tmp.dmg
	@hdiutil makehybrid -ov -hfs -hfs-volume-name Slouchy -o dist/Slouchy-tmp.dmg dist/dmg-root
	@hdiutil convert dist/Slouchy-tmp.dmg -format UDZO -o dist/Slouchy.dmg
	@rm -f dist/Slouchy-tmp.dmg
	@rm -rf dist/dmg-root
	@echo "=> Done! Shippable installer is at dist/Slouchy.dmg"
