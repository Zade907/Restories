# Reddit Shorts Factory - Makefile

.PHONY: install run run-upload dashboard schedule scrape test-tts clean

install:
	pip install -r requirements.txt

run:
	python main.py run --max 3

run-upload:
	python main.py run --max 3 --upload

dashboard:
	python main.py dashboard

schedule:
	python main.py schedule

scrape:
	python main.py scrape

test-tts:
	python main.py test-tts

clean:
	rm -rf output/audio/* output/videos/* output/subtitles/* output/scripts/* output/temp/*
	@echo "Output directories cleaned."

setup-dirs:
	mkdir -p assets/gameplay assets/thumbnails assets/fonts
	mkdir -p output/audio output/videos output/subtitles output/scripts
	mkdir -p logs database config
	cp .env.example .env
	@echo "✅ Directories created. Edit .env with your credentials."
