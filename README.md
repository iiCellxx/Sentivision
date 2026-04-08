# Sentivision

A real-time facial emotion detection system using YOLO for person detection and DeepFace for emotion recognition.

## Features

- Real-time person detection using YOLOv8
- Facial emotion recognition (happy, sad, angry, fear, surprise, disgust, neutral)
- Live video streaming via RTSP
- Emotion logging and reporting
- Web-based dashboard

## Requirements

- Python 3.8+
- OpenCV
- Ultralytics
- DeepFace
- Flask

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Set the RTSP URL environment variable:
```bash
export RTSP_URL="rtsp://user:pass@ip:port/profile0"
```

## Running

```bash
python app.py
```

Open http://localhost:5000 in your browser.

## License

MIT
