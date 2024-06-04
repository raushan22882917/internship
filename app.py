from flask import Flask, request, send_file, render_template, redirect, url_for
import os
import pandas as pd
import requests
from PIL import Image, ImageOps
from io import BytesIO
from urllib.parse import urlparse
import zipfile
import rembg
from rembg import remove

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
PROCESSED_FOLDER = 'processed'

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
                processed_image.convert('RGB').save(os.path.join(PROCESSED_FOLDER, f"{image_name}.jpg"), "JPEG")
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
        return image.resize((width, height)).convert('RGB')
    elif option == 'resize_background_remove':
        resized_image = image.resize((width, height))
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
    # Ensure the image is in RGBA mode to handle transparency
    image = image.convert("RGBA")
    # Create a white background image
    white_bg = Image.new("RGBA", image.size, (255, 255, 255))
    # Paste the image on top of the white background
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
        
        # Read CSV
        try:
            df = pd.read_csv(file_path)
        except Exception as e:
            return f"Error reading the CSV file: {e}"
        
        # Check if required columns exist
        if 'Image link' not in df.columns or 'Image Name' not in df.columns:
            return "'Image link' or 'Image Name' column not found in the CSV file."

        option = request.form.get('option')
        width = int(request.form.get('width')) if request.form.get('width') else None
        height = int(request.form.get('height')) if request.form.get('height') else None

        if option in ['resize', 'resize_background_remove'] and (width is None or height is None):
            return "Width and Height are required for resizing."

        process_images(df, option, width, height)

        # Create zip file
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


def download_images(csv_file_path):
    df = pd.read_csv(csv_file_path)
    image_paths = []

    if not os.path.exists('temp_images'):
        os.makedirs('temp_images')

    for index, row in df.iterrows():
        image_url = row.iloc[0]

        if 'dropbox.com' in image_url:
            image_url = image_url.replace('?dl=0', '?raw=1').replace('?rlkey', '?raw=1&rlkey')

        try:
            response = requests.get(image_url, stream=True)
            response.raise_for_status()

            image_name = f"image_{index}.jpg"
            image_path = os.path.join('temp_images', image_name)

            with open(image_path, 'wb') as out_file:
                for chunk in response.iter_content(1024):
                    out_file.write(chunk)

            image_paths.append(image_path)

        except requests.exceptions.RequestException as e:
            print(f"Failed to download {image_url}: {e}")

    return image_paths

def resize_image(image_path, size):
    with Image.open(image_path) as img:
        img = img.resize(size, Image.ANTIALIAS)
        img.save(image_path)

def remove_background(image_path):
    with Image.open(image_path) as img:
        img = remove(img)
        img.save(image_path)

def process_images(image_paths, action):
    for image_path in image_paths:
        if action == 'resize':
            resize_image(image_path, (800, 800))
        elif action == 'remove_bg':
            remove_background(image_path)
        elif action == 'resize_remove_bg':
            resize_image(image_path, (800, 800))
            remove_background(image_path)

def create_zip_file(image_paths):
    zip_file_path = 'downloaded_images.zip'
    with zipfile.ZipFile(zip_file_path, 'w') as zipf:
        for image_path in image_paths:
            zipf.write(image_path, os.path.basename(image_path))
    return zip_file_path

@app.route('/dropbox', methods=['GET', 'POST'])
def dropbox():
    if request.method == 'POST':
        csv_file = request.files['csv_file']
        action = request.form['action']

        csv_file_path = 'temp.csv'
        csv_file.save(csv_file_path)

        image_paths = download_images(csv_file_path)

        if action != 'download':
            process_images(image_paths, action)

        zip_file_path = create_zip_file(image_paths)

        return send_file(zip_file_path, as_attachment=True, download_name='downloaded_images.zip')

    return render_template('dropbox.html')
if __name__ == "__main__":
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    if not os.path.exists(PROCESSED_FOLDER):
        os.makedirs(PROCESSED_FOLDER)
    app.run(debug=True)
