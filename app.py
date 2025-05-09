import os
import redis
import random
import boto3
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)  # This allows all origins

# Redis setup
REDIS_URL = os.getenv("REDIS_URL")
SESSION_KEY = "otp_session"

# Redis client
try:
    redis_client: redis.Redis = redis.from_url(REDIS_URL)
except Exception as ex:
    raise Exception(f"Could not connect to Redis: {str(ex)}")

# AWS SNS setup
AWS_REGION = os.getenv("AWS_REGION")
sns_client = boto3.client(
    "sns",
    region_name=AWS_REGION,
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
)


@app.route('/send-otp', methods=['POST'])
def send_otp():
    data = request.json
    phone_number = data.get('phone_number')  # Expecting E.164 format (e.g., "+14155552671")

    if not phone_number:
        return jsonify({"status": "error", "message": "Missing phone number"}), 400

    # Ensure phone number has +91 prefix
    if not phone_number.startswith('+91'):
        phone_number = '+91' + phone_number.lstrip('0')  # Remove leading zero if present and add +91 prefix

    try:
        # Generate a 6-digit OTP
        otp = f"{random.randint(100000, 999999)}"

        # Send OTP via AWS SNS
        sns_client.publish(
            PhoneNumber=phone_number,
            Message=f"Your verification code is: {otp}"
        )

        # Store OTP in Redis with a 5-minute expiry
        redis_client.setex(f"otp:{phone_number}", 300, otp)

        return jsonify({"status": "success", "otp_sent_to": phone_number})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/verify-otp', methods=['POST'])
def verify_otp():
    data = request.json
    phone_number = data.get('phone_number')
    submitted_otp = data.get('otp')

    if not phone_number or not submitted_otp:
        return jsonify({"status": "error", "message": "Missing phone number or OTP"}), 400

    # Ensure phone number has +91 prefix
    if not phone_number.startswith('+91'):
        phone_number = '+91' + phone_number.lstrip('0')  # Remove leading zero if present and add +91 prefix

    try:
        otp_key = f"otp:{phone_number}"
        stored_otp = redis_client.get(otp_key)
        if stored_otp is None:
            return jsonify({"status": "error", "message": "OTP expired or not found"}), 404

        if stored_otp.decode() == submitted_otp:
            redis_client.delete(otp_key)
            return jsonify({"status": "success", "message": "OTP verified successfully"})
        else:
            return jsonify({"status": "error", "message": "Invalid OTP"}), 401
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True)
