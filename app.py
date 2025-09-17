import os
import redis
import random
import boto3
import logging
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
from botocore.exceptions import ClientError

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)  # This allows all origins

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Redis setup
REDIS_URL = os.getenv("REDIS_URL")
SESSION_KEY = "otp_session"

# Redis client
try:
    redis_client: redis.Redis = redis.from_url(REDIS_URL)
    logger.info("Connected to Redis successfully")
except Exception as ex:
    raise Exception(f"Could not connect to Redis: {str(ex)}")

# AWS SNS setup
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
sns_client = boto3.client(
    "sns",
    region_name=AWS_REGION,
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
)


def is_number_verified(phone_number):
    """Check if phone number is verified (stored in Redis)"""
    return redis_client.sismember("verified_numbers", phone_number)


def mark_number_as_verified(phone_number):
    """Mark a phone number as verified"""
    redis_client.sadd("verified_numbers", phone_number)


def send_sandbox_verification(phone_number):
    """Send AWS SNS sandbox verification to a new number"""
    try:
        response = sns_client.create_sms_sandbox_phone_number(
            PhoneNumber=phone_number,
            LanguageCode='en-US'
        )
        logger.info(f"Sandbox verification sent to {phone_number}")
        return True
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'InvalidParameter':
            logger.error(f"Invalid phone number: {phone_number}")
        elif error_code == 'OptedOut':
            logger.error(f"Phone number opted out: {phone_number}")
        elif error_code == 'ValidationException':
            logger.error(f"Phone number already exists in sandbox: {phone_number}")
            return True  # Already added, treat as success
        else:
            logger.error(f"SNS Error: {str(e)}")
        return False


def verify_sandbox_number(phone_number, verification_code):
    """Verify sandbox number with AWS verification code"""
    try:
        response = sns_client.verify_sms_sandbox_phone_number(
            PhoneNumber=phone_number,
            OneTimePassword=verification_code
        )
        mark_number_as_verified(phone_number)
        logger.info(f"Number verified: {phone_number}")
        return True
    except ClientError as e:
        logger.error(f"Verification failed for {phone_number}: {str(e)}")
        return False


def send_otp_sms(phone_number, otp):
    """Send OTP via AWS SNS with proper error handling"""
    try:
        response = sns_client.publish(
            PhoneNumber=phone_number,
            Message=f"Your verification code is: {otp}",
            MessageAttributes={
                'AWS.SNS.SMS.SMSType': {
                    'DataType': 'String',
                    'StringValue': 'Transactional'
                }
            }
        )
        return {"success": True, "message_id": response.get('MessageId')}
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'InvalidParameter':
            if 'phone number' in str(e).lower():
                return {"success": False, "error": "unverified_number", "message": "Number not verified in sandbox"}
            return {"success": False, "error": "invalid_number", "message": "Invalid phone number format"}
        elif error_code == 'OptedOut':
            return {"success": False, "error": "opted_out", "message": "Phone number has opted out of SMS"}
        else:
            return {"success": False, "error": "aws_error", "message": str(e)}


@app.route('/send-otp', methods=['POST'])
def send_otp():
    data = request.json
    phone_number = data.get('phone_number')

    if not phone_number:
        return jsonify({"status": "error", "message": "Missing phone number"}), 400

    # Ensure phone number has +91 prefix
    if not phone_number.startswith('+91'):
        phone_number = '+91' + phone_number.lstrip('0')

    try:
        # Generate a 6-digit OTP
        otp = f"{random.randint(100000, 999999)}"

        # Check if number is verified or try to send OTP directly
        sms_result = send_otp_sms(phone_number, otp)

        if sms_result["success"]:
            # OTP sent successfully
            redis_client.setex(f"otp:{phone_number}", 300, otp)
            mark_number_as_verified(phone_number)  # Mark as verified for future use

            return jsonify({
                "status": "success",
                "otp_sent_to": phone_number,
                "message_id": sms_result.get("message_id")
            })

        elif sms_result["error"] == "unverified_number":
            # Number needs verification in sandbox
            verification_sent = send_sandbox_verification(phone_number)

            if verification_sent:
                # Store the OTP for later use after verification
                redis_client.setex(f"pending_otp:{phone_number}", 600, otp)

                return jsonify({
                    "status": "verification_required",
                    "message": "This number needs to be verified first. Check your SMS for verification code.",
                    "phone_number": phone_number,
                    "action": "verify_number"
                }), 202
            else:
                return jsonify({
                    "status": "error",
                    "message": "Failed to send verification SMS"
                }), 500

        else:
            # Other SMS errors
            return jsonify({
                "status": "error",
                "message": sms_result["message"]
            }), 400

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/verify-number', methods=['POST'])
def verify_number():
    """Verify a new number with AWS sandbox verification code"""
    data = request.json
    phone_number = data.get('phone_number')
    verification_code = data.get('verification_code')

    if not phone_number or not verification_code:
        return jsonify({
            "status": "error",
            "message": "Missing phone number or verification code"
        }), 400

    # Ensure phone number has +91 prefix
    if not phone_number.startswith('+91'):
        phone_number = '+91' + phone_number.lstrip('0')

    try:
        if verify_sandbox_number(phone_number, verification_code):
            # Number verified, now check if there's a pending OTP to send
            pending_otp = redis_client.get(f"pending_otp:{phone_number}")

            if pending_otp:
                # Send the pending OTP
                otp = pending_otp.decode()
                sms_result = send_otp_sms(phone_number, otp)

                if sms_result["success"]:
                    # Move from pending to active OTP
                    redis_client.delete(f"pending_otp:{phone_number}")
                    redis_client.setex(f"otp:{phone_number}", 300, otp)

                    return jsonify({
                        "status": "success",
                        "message": "Number verified and OTP sent successfully",
                        "otp_sent_to": phone_number
                    })

            return jsonify({
                "status": "success",
                "message": "Number verified successfully. You can now receive OTPs.",
                "phone_number": phone_number
            })
        else:
            return jsonify({
                "status": "error",
                "message": "Invalid verification code"
            }), 400

    except Exception as e:
        logger.error(f"Error during verification: {str(e)}")
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
        phone_number = '+91' + phone_number.lstrip('0')

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
        logger.error(f"Error verifying OTP: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/resend-otp', methods=['POST'])
def resend_otp():
    """Resend OTP for verified numbers"""
    data = request.json
    phone_number = data.get('phone_number')

    if not phone_number:
        return jsonify({"status": "error", "message": "Missing phone number"}), 400

    # Ensure phone number has +91 prefix
    if not phone_number.startswith('+91'):
        phone_number = '+91' + phone_number.lstrip('0')

    # Check if number is verified
    if not is_number_verified(phone_number):
        return jsonify({
            "status": "error",
            "message": "Number not verified. Please verify first."
        }), 403

    try:
        # Generate new OTP
        otp = f"{random.randint(100000, 999999)}"

        # Send OTP
        sms_result = send_otp_sms(phone_number, otp)

        if sms_result["success"]:
            redis_client.setex(f"otp:{phone_number}", 300, otp)
            return jsonify({
                "status": "success",
                "otp_sent_to": phone_number,
                "message": "OTP resent successfully"
            })
        else:
            return jsonify({
                "status": "error",
                "message": sms_result["message"]
            }), 400

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True)