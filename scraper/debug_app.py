from flask import Flask, jsonify
import os

app = Flask(__name__)

@app.route("/ping", methods=["GET"])
def ping():
    return "PONG"

@app.route("/debug", methods=["POST"])
def debug():
    print("[DEBUG] Request received at /debug")
    return jsonify({"status": "received", "env": list(os.environ.keys())})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5051) # Different port for testing
