from flask import Flask, request, send_file, render_template, redirect, url_for
import os
import pandas as pd
import requests
from PIL import Image
from io import BytesIO
from urllib.parse import urlparse
import zipfile
import rembg
from rembg import remove
from celery import Celery
import time
import fitz  
import io
from PIL import Image

app = Flask(__name__)

UPLOAD_FOLDER = 'uploads'
PROCESSED_FOLDER = 'processed'
DROPBOX_TEMP_FOLDER = 'dropbox_temp'
DROPBOX_ZIP_FILENAME = 'dropbox_downloaded_images.zip'

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['PROCESSED_FOLDER'] = PROCESSED_FOLDER
app.config['DROPBOX_TEMP_FOLDER'] = DROPBOX_TEMP_FOLDER

def is_valid_url(url):
    parsed = urlparse(url)
    return bool(parsed.netloc) and bool(parsed.scheme)

def download_image(url):
    try:
        response = requests.get(url)
        if response.status_code == 200:
            return Image.open(BytesIO(response.content))
        else:
            print(f"Failed to download image from URL: {url} with status code {response.status_code}")
            return None
    except Exception as e:
        print(f"Error occurred while downloading image from URL: {url}\n{e}")
        return None

def process_images(df, option, width=None, height=None):
    if not os.path.exists(PROCESSED_FOLDER):
        os.makedirs(PROCESSED_FOLDER)
    
    for index, row in df.iterrows():
        url = row['Image link']
        image_name = row['Image Name']
        
        if pd.notna(url) and pd.notna(image_name) and is_valid_url(url):
            image = download_image(url)
            if image:
                processed_image = process_image(image, option, width, height)
                processed_image.convert('RGB').save(os.path.join(PROCESSED_FOLDER, f"{image_name}.jpg"), "JPEG", quality=95)
                print(f"Image '{image_name}' processed and saved successfully.")
        else:
            print(f"Invalid or missing URL or image name for row {index + 1}. Skipping this row.")

def process_image(image, option, width=None, height=None):
    if option == 'original':
        return image.convert('RGB')
    elif option == 'background_remove':
        removed_bg_image = Image.open(BytesIO(rembg.remove(image_to_bytes(image))))
        return add_white_background(removed_bg_image)
    elif option == 'resize':
        return image.resize((width, height), Image.LANCZOS).convert('RGB')
    elif option == 'resize_background_remove':
        resized_image = image.resize((width, height), Image.LANCZOS)
        removed_bg_image = Image.open(BytesIO(rembg.remove(image_to_bytes(resized_image))))
        return add_white_background(removed_bg_image)
    else:
        return image.convert('RGB')

def image_to_bytes(image):
    img_byte_arr = BytesIO()
    image.save(img_byte_arr, format='PNG')
    img_byte_arr = img_byte_arr.getvalue()
    return img_byte_arr

def add_white_background(image):
    image = image.convert("RGBA")
    white_bg = Image.new("RGBA", image.size, (255, 255, 255))
    white_bg.paste(image, (0, 0), image)
    return white_bg.convert("RGB")

@app.route('/')
def HOME():
    return render_template('index.html')

@app.route('/image')
def index():
    return render_template('imagedownloder.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files or request.files['file'].filename == '':
        return "No file selected"

    file = request.files['file']
    if file:
        file_path = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(file_path)
        
        try:
            df = pd.read_csv(file_path)
        except Exception as e:
            return f"Error reading the CSV file: {e}"
        
        if 'Image link' not in df.columns or 'Image Name' not in df.columns:
            return "'Image link' or 'Image Name' column not found in the CSV file."

        option = request.form.get('option')
        width = int(request.form.get('width')) if request.form.get('width') else None
        height = int(request.form.get('height')) if request.form.get('height') else None

        if option in ['resize', 'resize_background_remove'] and (width is None or height is None):
            return "Width and Height are required for resizing."

        process_images(df, option, width, height)

        zip_filename = "processed_images.zip"
        zip_path = os.path.join(PROCESSED_FOLDER, zip_filename)
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for root, dirs, files in os.walk(PROCESSED_FOLDER):
                for file in files:
                    if file != zip_filename:
                        zipf.write(os.path.join(root, file), file)

        return redirect(url_for('download_zip', filename=zip_filename))

@app.route('/download/<filename>')
def download_zip(filename):
    return send_file(os.path.join(PROCESSED_FOLDER, filename), as_attachment=True)

def download_dropbox_images(csv_file_path):
    df = pd.read_csv(csv_file_path)
    image_paths = []

    if not os.path.exists(DROPBOX_TEMP_FOLDER):
        os.makedirs(DROPBOX_TEMP_FOLDER)

    for index, row in df.iterrows():
        image_url = row['Image link']

        if 'dropbox.com' in image_url:
            image_url = image_url.replace('?dl=0', '?raw=1').replace('?rlkey', '?raw=1&rlkey')

        try:
            response = requests.get(image_url, stream=True)
            response.raise_for_status()

            image_name = row['Image Name']
            image_path = os.path.join(DROPBOX_TEMP_FOLDER, image_name + ".jpg")

            with Image.open(BytesIO(response.content)) as img:
                img.convert('RGB').save(image_path, 'JPEG', quality=95)

            image_paths.append(image_path)

        except requests.exceptions.RequestException as e:
            print(f"Failed to download {image_url}: {e}")

    return image_paths

def resize_dropbox_image(image_path, size):
    with Image.open(image_path) as img:
        img = img.resize(size, Image.LANCZOS).convert('RGB')
        img.save(image_path, 'JPEG', quality=95)

def remove_dropbox_background(image_path):
    with Image.open(image_path) as img:
        img = remove(img)
        img = add_white_background(img)
        img.convert('RGB').save(image_path, 'JPEG', quality=95)

def process_dropbox_images(image_paths, action):
    for image_path in image_paths:
        if action == 'resize':
            resize_dropbox_image(image_path, (800, 800))
        elif action == 'remove_bg':
            remove_dropbox_background(image_path)
        elif action == 'resize_remove_bg':
            resize_dropbox_image(image_path, (800, 800))
            remove_dropbox_background(image_path)

def create_dropbox_zip_file(image_paths):
    zip_file_path = os.path.join(DROPBOX_TEMP_FOLDER, DROPBOX_ZIP_FILENAME)
    with zipfile.ZipFile(zip_file_path, 'w') as zipf:
        for image_path in image_paths:
            zipf.write(image_path, os.path.basename(image_path))
    return zip_file_path

@app.route('/dropbox', methods=['GET', 'POST'])
def dropbox():
    if request.method == 'POST':
        csv_file = request.files['csv_file']
        action = request.form['action']

        csv_file_path = os.path.join(DROPBOX_TEMP_FOLDER, 'temp.csv')
        csv_file.save(csv_file_path)

        image_paths = download_dropbox_images(csv_file_path)

        if action != 'download':
            process_dropbox_images(image_paths, action)

        zip_file_path = create_dropbox_zip_file(image_paths)

        return send_file(zip_file_path, as_attachment=True, download_name=DROPBOX_ZIP_FILENAME)

    return render_template('dropbox.html')

celery = Celery(__name__)
celery.conf.broker_url = 'redis://localhost:6379/0'

@celery.task
def delete_old_files():
    twenty_four_hours_ago = time.time() - (24 * 60 * 60)

    for folder in [UPLOAD_FOLDER, PROCESSED_FOLDER, DROPBOX_TEMP_FOLDER]:
        for filename in os.listdir(folder):
            file_path = os.path.join(folder, filename)
            if os.path.isfile(file_path):
                if os.path.getmtime(file_path) < twenty_four_hours_ago:
                    os.remove(file_path)

@celery.on_after_configure.connect
def setup_periodic_tasks(sender, **kwargs):
    sender.add_periodic_task(86400, delete_old_files.s(), name='delete old files every 24 hours')


#########################################################################Unique#####################################
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

@app.route('/unique')
def unique():
    return render_template('uniqe.html')

@app.route('/uploadunique', methods=['POST'])
def upload_file_unique():
    if 'file' not in request.files:
        return redirect(request.url)
    file = request.files['file']
    if file.filename == '':
        return redirect(request.url)
    if file:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(file_path)
        
        # Process the CSV file
        df = pd.read_csv(file_path, encoding='ISO-8859-1')

        df['Unique_Or_Duplicate'] = 'Unique'
        df['Duplicate_Of'] = None

        duplicate_tracker = {}

        for index, row in df.iterrows():
            identifier = (row['uom'], row['MSN_Description'])
            if identifier in duplicate_tracker:
                original_msn = duplicate_tracker[identifier]
                df.at[index, 'Unique_Or_Duplicate'] = 'Duplicate'
                df.at[index, 'Duplicate_Of'] = original_msn
            else:
                duplicate_tracker[identifier] = row['MSN']

        output_path = os.path.join(app.config['UPLOAD_FOLDER'], 'output3.csv')
        df.to_csv(output_path, index=False)

        return send_file(output_path, as_attachment=True, download_name='output3.csv')
    
    
###########################################################image from pdf #########################################################
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['EXTRACT_FOLDER'] = 'extracted_images_2'
app.config['ZIP_FOLDER'] = 'zipped_images'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['EXTRACT_FOLDER'], exist_ok=True)
os.makedirs(app.config['ZIP_FOLDER'], exist_ok=True)

def extract_images_from_pdf(pdf_path, output_folder):
    pdf_document = fitz.open(pdf_path)
    for page_num in range(len(pdf_document)):
        page = pdf_document.load_page(page_num)
        images = page.get_images(full=True)
        for img_index, img in enumerate(images):
            xref = img[0]
            base_image = pdf_document.extract_image(xref)
            image_bytes = base_image["image"]
            image_ext = base_image["ext"]
            image = Image.open(io.BytesIO(image_bytes))
            image_filename = f"page_{page_num+1}_img_{img_index+1}.{image_ext}"
            image.save(os.path.join(output_folder, image_filename))
            print(f"Saved image: {image_filename}")

def create_zip_file(source_folder, zip_filename):
    if os.path.exists(zip_filename):
        os.remove(zip_filename)
    with zipfile.ZipFile(zip_filename, 'w') as zipf:
        for root, dirs, files in os.walk(source_folder):
            for file in files:
                zipf.write(os.path.join(root, file), arcname=file)
    print(f"Created zip file: {zip_filename}")

@app.route('/pdfimage', methods=['GET', 'POST'])
def upload_file_image():
    if request.method == 'POST':
        if 'file' not in request.files:
            return redirect(request.url)
        file = request.files['file']
        if file.filename == '':
            return redirect(request.url)
        if file:
            pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
            file.save(pdf_path)
            extract_images_from_pdf(pdf_path, app.config['EXTRACT_FOLDER'])

            zip_filename = os.path.join(app.config['ZIP_FOLDER'], 'extracted_images.zip')
            create_zip_file(app.config['EXTRACT_FOLDER'], zip_filename)
            return redirect(url_for('download_zip_image', filename='extracted_images.zip'))
    return render_template('pdf_to_image.html')

@app.route('/downloads/<filename>')
def download_zip_image(filename):
    zip_path = os.path.join(app.config['ZIP_FOLDER'], filename)
    return send_file(zip_path, as_attachment=True)
    
if __name__ == "__main__":
    for folder in [UPLOAD_FOLDER, PROCESSED_FOLDER, DROPBOX_TEMP_FOLDER]:
        if not os.path.exists(folder):
            os.makedirs(folder)
    app.run(debug=True)
