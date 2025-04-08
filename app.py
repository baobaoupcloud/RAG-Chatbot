from flask import Flask, session, redirect, url_for, render_template, request, flash, Response
import time
from flask_session import Session
from authlib.integrations.flask_client import OAuth
import boto3, io, jwt, requests
from jwt import PyJWKClient
from botocore.exceptions import ClientError

# Configuration 

# 1) Flask secret key 
FLASK_SECRET_KEY    = "-FLASK-SECRET"

# 2) Cognito App details
COGNITO_REGION      = "us-east-1"
COGNITO_USER_POOL   = "THE_USER_POOL_ID"  # e.g. us-east-1_ABC123456
COGNITO_CLIENT_ID   = "THE_CLIENT_ID"     # e.g. 1rqk3m6fjtu2598adi9lc8khom
COGNITO_CLIENT_SECRET = "THE_CLIENT_SECRET"  # e.g. nkwmredtu28329fjiwer

# 3) The domain set under Cognito → App integration → Domain name
COGNITO_DOMAIN      = "THE_COGNITO_DOMAIN" # e.g. us-east-1nhsphckri.auth.us-east-1.amazoncognito.com"

# 4) Your S3 bucket
S3_BUCKET           = "S3_BUCKET_NAME"  # e.g. my-bucket-name

# 5) Your Bedrock knowledge base ID
KNOWLEDGE_BASE_ID   = "KNOWLEDGE_BASE_ID"  # e.g. 1234567890abcdefg

# 6) Telegram
TELEGRAM_BOT_TOKEN = "TELEGRAM_BOT_TOKEN"  # e.g. 123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
TELEGRAM_CHAT_ID   = "TELEGRAM_CHAT_ID"    # e.g. -1234567890

# Flask & Session Setup

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

# Use server-side sessions so we can store chat history reliably
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_FILE_DIR"] = "./.flask_session"
app.config["SESSION_PERMANENT"] = True
Session(app)

# OAuth (Authlib) Setup

oauth = OAuth(app)
oauth.register(
    name="cognito",
    client_id=COGNITO_CLIENT_ID,
    client_secret=COGNITO_CLIENT_SECRET,

    # Hosted UI endpoints:
    authorize_url=f"https://{COGNITO_DOMAIN}/oauth2/authorize",
    access_token_url=f"https://{COGNITO_DOMAIN}/oauth2/token",
    userinfo_endpoint=f"https://{COGNITO_DOMAIN}/oauth2/userInfo",

    # JWKS endpoint for verifying ID tokens:
    jwks_uri=(
        f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"
        f"{COGNITO_USER_POOL}/.well-known/jwks.json"
    ),

    client_kwargs={"scope": "openid profile email"},
)

def verify_jwt(token: str) -> dict:
    jwks = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL}/.well-known/jwks.json"
    jwk_client = PyJWKClient(jwks)
    key = jwk_client.get_signing_key_from_jwt(token).key
    decoded = jwt.decode(
        token, key,
        algorithms=["RS256"],
        audience=COGNITO_CLIENT_ID,
        issuer=f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL}"
    )
    if decoded.get("token_use") != "id":
        raise ValueError("Not an ID token")
    return decoded

# Utility Functions

def upload_to_s3(file_obj, filename):
    s3 = boto3.client("s3", region_name=COGNITO_REGION)
    try:
        s3.upload_fileobj(file_obj, S3_BUCKET, filename)
        return True
    except ClientError:
        return False

def call_bedrock(prompt: str) -> str:
    client = boto3.client("bedrock-agent-runtime", region_name=COGNITO_REGION)
    resp = client.retrieve_and_generate(
        input={"text": prompt},
        retrieveAndGenerateConfiguration={
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": KNOWLEDGE_BASE_ID,
                "modelArn": "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-text-premier-v1:0",
            },
        },
    )
    return resp["output"]["text"]

def build_prompt(history, question):
    s = ""
    for turn in history:
        s += f"User: {turn['user']}\nBot: {turn['bot']}\n"
    s += f"User: {question}\nBot: "
    return s

def send_to_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }
    try:
        resp = requests.post(url, data=payload, timeout=5)
        resp.raise_for_status()
    except Exception as e:
        app.logger.error("Telegram send failed: %s", e)

# Routes

@app.before_request
def make_session_permanent():
    session.permanent = True

@app.route("/")
def index():
    session.setdefault("history", [])
    user = session.get("user")
    return render_template("index.html",
        user=user,
        history=session["history"]
    )

@app.route("/login")
def login():
    redirect_uri = url_for("authorize", _external=True)
    return oauth.cognito.authorize_redirect(redirect_uri)

@app.route("/authorize")
def authorize():
    token = oauth.cognito.authorize_access_token()
    if not token:
        flash("Authorization failed.", "error")
        return redirect(url_for("index"))

    id_token = token.get("id_token")
    try:
        decoded = verify_jwt(id_token)
    except Exception as e:
        flash(f"Token verification failed: {e}", "error")
        return redirect(url_for("index"))


    user = {
        "sub": decoded["sub"],
        "email": decoded.get("email"),
        "email_verified": decoded.get("email_verified"),
    }

    session["user"] = user
    session["user_groups"] = decoded.get("cognito:groups", [])
    flash(f"Welcome, {user.get('email', 'user')}!", "success")
    return redirect(url_for("index"))

@app.route("/logout")
def logout():
    session.clear()

    logout_url = (
        f"https://{COGNITO_DOMAIN}/logout"  
        f"?client_id={COGNITO_CLIENT_ID}"
        f"&logout_uri={url_for('index', _external=True)}"
    )

    return redirect(logout_url)


@app.route("/upload", methods=["POST"])
def upload():
    if not session.get("user"):
        return redirect(url_for("index"))

    f = request.files.get("file")
    if not f or not f.filename.endswith(".md"):
        flash("Please upload a .md file", "error")
        return redirect(url_for("index"))

    buf = io.BytesIO(f.read())
    ok = upload_to_s3(buf, f.filename)
    flash("Upload successful" if ok else "Upload failed", "success" if ok else "error")
    return redirect(url_for("index"))

@app.route("/stream_chat", methods=["POST"])
def stream_chat():
    if "user" not in session:
        return Response(status=401)

    data = request.get_json() or {}
    question = data.get("question", "").strip()
    if not question:
        return Response(status=204)

    history = session.get("history", [])

    prompt = ""
    for turn in history:
        prompt += f"User: {turn['user']}\nBot: {turn['bot']}\n"
    prompt += f"User: {question}\nBot: "

    answer = call_bedrock(prompt)

    history.append({"user": question, "bot": answer})
    session["history"] = history
    session.modified = True

    send_to_telegram(f"Chat by {session['user']['email']}\nUser: {question}\nBot: {answer}")

    def generate_chars():
        for ch in answer:
            yield ch
            time.sleep(0.02)
    return Response(generate_chars(), mimetype="text/plain")

if __name__ == "__main__":
    app.run(debug=True, port=8501)
