import datetime
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from jinja2 import Environment, FileSystemLoader, TemplateNotFound, TemplateSyntaxError

class EmailData:
    def __init__(self, email, code, time, ip_address, device):
        self.email = email
        self.code = code
        self.time = time
        self.ip_address = ip_address
        self.device = device

def send_verification_email(to: str, code: str, ip_address: str, device: str) -> bool:
    subject = "SnapGoated - Your Verification Code"
    template_path = os.path.join(os.path.dirname(__file__), '..', 'resource', 'email_template.html')

    try:
        html_body = create_email_content(to, code, ip_address, device, template_path)
    except Exception as e:
        print(f"Error creating email content: {e}")
        return False

    return send_email(to, subject, html_body)

def create_email_content(to: str, code: str, ip_address: str, device: str, template_path: str) -> str:
    data = EmailData(
        email=mask_email(to),
        code=code,
        time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ip_address=ip_address,
        device=device
    )

    try:
        html_body = load_email_template(template_path, data)
    except Exception as e:
        raise e

    return html_body

def load_email_template(template_path: str, data: EmailData) -> str:
    try:
        # Set up the Jinja2 environment
        template_dir, template_file = os.path.split(template_path)
        env = Environment(loader=FileSystemLoader(template_dir))

        # Load the template
        template = env.get_template(template_file)

        # Render the template with the provided data
        html_body = template.render(
            email=data.email,
            code=data.code,
            time=data.time,
            ip_address=data.ip_address,
            device=data.device
        )
    except TemplateNotFound:
        raise FileNotFoundError(f"Template {template_file} not found in {template_dir}")
    except TemplateSyntaxError as e:
        raise SyntaxError(f"Template syntax error in {template_file} at line {e.lineno}: {e.message}")
    except Exception as e:
        raise e

    return html_body

def send_email(to: str, subject: str, html_body: str) -> bool:
    try:
        # Create a new SES session
        ses_client = boto3.client('ses', region_name='ap-southeast-1')

        # Create the email input
        response = ses_client.send_email(
            Destination={
                'ToAddresses': [to],
            },
            Message={
                'Body': {
                    'Html': {
                        'Charset': 'UTF-8',
                        'Data': html_body,
                    },
                },
                'Subject': {
                    'Charset': 'UTF-8',
                    'Data': subject,
                },
            },
            Source='no-reply@snapgoated.com',
        )
    except (BotoCoreError, ClientError) as e:
        print(f"Error sending email: {e}")
        return False

    return True

def mask_email(email: str) -> str:
    parts = email.split("@")
    if len(parts) != 2:
        return email  # Return the original email if it doesn't have exactly one '@' character

    local, domain = parts
    if len(local) <= 2:
        return email  # Return the original email if the local part is too short to mask

    masked_local = local[:2] + "*" * (len(local) - 4) + local[-2:]
    return masked_local + "@" + domain