import base64
from flask import Flask, render_template, request, send_file, redirect, url_for, flash, session
from werkzeug.utils import secure_filename
from docx import Document
import os
import io
from datetime import datetime
import re
import docx2txt
import re
from collections import defaultdict
from flask import Response
from docx.shared import Inches
from docx.oxml.shared import qn
from docx.oxml.ns import nsdecls
import uuid
import glob



app = Flask(__name__)
app.secret_key = 'your-secret-key-here'
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024  # 2MB limit
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'docx'}
app.config['PHOTO_EXTRACTION'] = True

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def parse_resume(filepath):
    """
    Comprehensive resume parser with improved name extraction logic
    """
    # Extract all text from the document
    text = docx2txt.process(filepath)

    # Initialize structured data storage
    resume_data = {
        'contact': {},
        'summary': '',
        'education': [],
        'experience': [],
        'skills': [],
        'projects': [],
        'achievements': [],
        'certifications': [],
        'photo': None
    }

    if app.config['PHOTO_EXTRACTION']:
        extraction_result = docx2txt.process(filepath)
        text = extraction_result if isinstance(extraction_result, str) else extraction_result.get('text', '')
        resume_data['photo'] = extract_photo_from_docx(filepath)
    else:
        text = docx2txt.process(filepath)

    # ====== IMPROVED NAME EXTRACTION ======
    def extract_name(text):
        """
        Enhanced name extraction logic that:
        1. Looks for proper name patterns in the first few lines
        2. Excludes common non-name headers
        3. Handles various name formats
        """
        # Common resume headers to exclude
        excluded_headers = {
            'resume', 'cv', 'curriculum vitae', 'personal profile',
            'contact information', 'professional profile'
        }

        # Patterns that might indicate a name line
        name_patterns = [
            r'^[A-Z][a-z]+ [A-Z][a-z]+$',  # First Last
            r'^[A-Z][a-z]+ [A-Z]\. [A-Z][a-z]+$',  # First M. Last
            r'^[A-Z][a-z]+ [A-Z][a-z]+ [A-Z][a-z]+$',  # First Middle Last
            r'^[A-Z][a-z]+, [A-Z][a-z]+$',  # Last, First
        ]

        # Check first 5 non-empty lines
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        for line in lines[:5]:
            # Skip if line matches excluded headers
            if line.lower() in excluded_headers:
                continue

            # Check for email/phone - these usually come after name
            if '@' in line or 'phone' in line.lower() or 'tel' in line.lower():
                continue

            # Check for name patterns
            for pattern in name_patterns:
                if re.fullmatch(pattern, line):
                    return line

            # Simple heuristic: if line has 2-4 title-cased words
            words = line.split()
            if 2 <= len(words) <= 4:
                if all(word.istitle() for word in words):
                    # Exclude lines with common non-name words
                    non_name_words = {'linkedin', 'github', 'portfolio', 'mobile'}
                    if not any(word.lower() in non_name_words for word in words):
                        return line

        # Fallback: return the first line that doesn't look like a header
        for line in lines[:5]:
            if not any(header in line.lower() for header in excluded_headers):
                if len(line.split()) >= 2:  # At least two words
                    return line

        return None

    # Extract name using improved logic
    name = extract_name(text)
    if name:
        resume_data['contact']['name'] = name

    # ====== 2. SECTION-BASED PARSING ======
        # Rest of your parsing code remains the same...
        # ====== 2. SECTION-BASED PARSING ======
        sections = re.split(r'\n\s*\n', text)  # Split by double newlines
        current_section = None

        # Define section patterns with priority order
        section_patterns = [
            ('summary',
             r'^(profile\s*summary|summary|about|objective|PROFILE|career\s*objectives|PROFESSIONAL\s*SUMMARY)'),
            ('experience', r'^(work\s*experience|experience|professional\s*experience)'),
            ('education', r'^(education|academic\s*background|qualifications|academics)'),
            ('skills', r'^(skills|technical\s*skills|competencies)'),
            ('projects', r'^(projects|key\s*projects)'),
            ('achievements', r'^(achievements|awards|honors)'),
            ('certifications', r'^(certifications|licenses|courses)')
        ]

        for content in sections:
            content = content.strip()
            if not content:
                continue

            # Check if this is a section header
            section_found = False
            for section_name, pattern in section_patterns:
                if re.match(pattern, content, re.IGNORECASE):
                    current_section = section_name
                    section_found = True
                    # Initialize section data structure if needed
                    if isinstance(resume_data[current_section], list):
                        resume_data[current_section] = []
                    else:
                        resume_data[current_section] = ''
                    break

            if not section_found and current_section:
                # Process content according to current section
                if current_section == 'summary':
                    resume_data[current_section] += ' ' + content
                elif current_section in ['skills', 'certifications']:
                    # Split by commas, bullets, or newlines
                    items = re.split(r'[,•;\n]', content)
                    resume_data[current_section].extend(
                        [item.strip() for item in items if item.strip()]
                    )
                else:  # experience, education, projects, achievements
                    if '\n' in content:  # Multi-line entries
                        entries = [e.strip() for e in content.split('\n') if e.strip()]
                        resume_data[current_section].extend(entries)
                    else:
                        resume_data[current_section].append(content)

    # ====== 3. SPECIAL HANDLING FOR EDUCATION ======
    # Try to extract structured education data even if not properly sectioned
    if not resume_data['education']:
        edu_pattern = r'(.+?)\s*(?:-|\||\t)\s*(.+?)\s*(?:-|\||\t)\s*(.+?)\s*(?:-|\||\t)\s*(\d{4})\s*(?:-|\||\t)\s*([\d\.%]+)'
        matches = re.finditer(edu_pattern, text)
        for match in matches:
            institution, degree, university, year, grade = match.groups()
            resume_data['education'].append(
                f"{institution} | {degree} | {university} | {year} | {grade}"
            )

    # ====== 4. CLEANING AND POST-PROCESSING ======
    # Clean summary section
    if resume_data['summary']:
        resume_data['summary'] = re.sub(
            r'^(profile\s*summary|summary|about|objective|profile|career\s*Objectives)[:\s-]*',
            '',
            resume_data['summary'],
            flags=re.IGNORECASE
        ).strip()

    # Remove empty sections
    for section in list(resume_data.keys()):
        if isinstance(resume_data[section], (list, dict)) and not resume_data[section]:
            del resume_data[section]
        elif isinstance(resume_data[section], str) and not resume_data[section]:
            del resume_data[section]

    return resume_data


def extract_photo_from_docx(filepath):
    """Extract the first JPG/JPEG image from a DOCX file and save as image file"""
    try:
        import zipfile
        import uuid

        with zipfile.ZipFile(filepath, 'r') as docx_zip:
            # Only look for JPG/JPEG files in the media directory
            image_files = [f for f in docx_zip.namelist()
                          if f.startswith('word/media/') and
                          f.lower().endswith(('.jpg', '.jpeg'))]

            if image_files:
                # Get the first JPG/JPEG image found
                image_data = docx_zip.read(image_files[0])

                # Generate unique filename
                photo_id = str(uuid.uuid4())

                # Save image file directly with .jpg extension
                photo_filename = f"photo_{photo_id}.jpg"
                photo_path = os.path.join(app.config['UPLOAD_FOLDER'], photo_filename)

                with open(photo_path, 'wb') as f:
                    f.write(image_data)

                return photo_id

        return None

    except Exception as e:
        app.logger.error(f"Error extracting photo: {str(e)}")
        return None

def create_output_doc(sections, original_filename):
    """Create well-formatted DOCX output with photo"""
    doc = Document()

    # Header
    doc.add_heading('Parsed Resume Information', level=1)
    doc.add_paragraph(f"Original file: {original_filename}")
    doc.add_paragraph(f"Parsed on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Add photo if available
    if 'photo_id' in session and session['photo_id']:
        try:
            photo_pattern = os.path.join(app.config['UPLOAD_FOLDER'], f"photo_{session['photo_id']}.*")
            photo_files = glob.glob(photo_pattern)
            if photo_files:
                photo_path = photo_files[0]

                # Add photo to document
                doc.add_heading('Profile Photo', level=2)
                paragraph = doc.add_paragraph()
                run = paragraph.add_run()

                # Add the image to the document
                run.add_picture(photo_path, width=Inches(1.5))
        except Exception as e:
            app.logger.error(f"Error adding photo to document: {str(e)}")

    # Contact Information
    if 'contact' in sections:
        doc.add_heading('Contact Information', level=2)
        contact_table = doc.add_table(rows=0, cols=2)
        for field in ['name', 'email', 'phone', 'address', 'linkedin']:
            if sections['contact'].get(field):
                row = contact_table.add_row()
                row.cells[0].text = field.capitalize() + ':'
                row.cells[1].text = sections['contact'][field]

    # Rest of the sections (summary, skills, experience, etc.)
    if sections.get('summary'):
        doc.add_heading('Profile Summary', level=2)
        doc.add_paragraph(sections['summary'])

    if sections.get('skills'):
        doc.add_heading('Skills', level=2)
        doc.add_paragraph(', '.join(sections['skills']))

    if sections.get('experience'):
        doc.add_heading('Work Experience', level=2)
        for exp in sections['experience']:
            doc.add_paragraph(exp, style='List Bullet')

    if sections.get('education'):
        doc.add_heading('Education', level=2)
        for edu in sections['education']:
            doc.add_paragraph(edu, style='List Bullet')

    if sections.get('certifications'):
        doc.add_heading('Certifications', level=2)
        for cert in sections['certifications']:
            doc.add_paragraph(cert, style='List Bullet')

    if sections.get('projects'):
        doc.add_heading('Projects', level=2)
        for proj in sections['projects']:
            doc.add_paragraph(proj, style='List Bullet')

    # Save to memory
    file_stream = io.BytesIO()
    doc.save(file_stream)
    file_stream.seek(0)
    return file_stream


@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file selected')
            return redirect(request.url)

        file = request.files['file']

        if file.filename == '':
            flash('No file selected')
            return redirect(request.url)

        if file and allowed_file(file.filename):
            try:
                filename = secure_filename(file.filename)
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(filepath)

                parsed_data = parse_resume(filepath)

                photo_id = None
                if parsed_data.get('photo'):
                    photo_id = parsed_data['photo']
                    del parsed_data['photo']
                session['parsed_data'] = parsed_data
                session['original_filename'] = filename
                session['photo_id'] = photo_id

                os.remove(filepath)
                return redirect(url_for('preview'))

            except Exception as e:
                flash(f'Error processing file: {str(e)}')
                return redirect(request.url)
        else:
            flash('Only .docx files are allowed (max 2MB)')
            return redirect(request.url)

    return render_template('upload.html')

@app.route('/photo/<photo_id>')
def serve_photo(photo_id):
    try:
        photo_path = os.path.join(app.config['UPLOAD_FOLDER'], f"photo_{photo_id}.jpg")
        if os.path.exists(photo_path):
            return send_file(photo_path, mimetype='image/jpeg')
        return "Photo not found", 404
    except Exception as e:
        print(f"Error serving photo: {str(e)}")
        return "Error loading photo", 500

@app.route('/preview')
def preview():
    if 'parsed_data' not in session:
        flash('No resume to preview. Please upload a file first.')
        return redirect(url_for('upload_file'))

    return render_template('preview.html',
                           parsed_text=session['parsed_data'],
                           original_filename=session['original_filename'])


@app.route('/download')
def download_file():
    if 'parsed_data' not in session:
        flash('No resume to download. Please upload a file first.')
        return redirect(url_for('upload_file'))

    try:
        output = create_output_doc(
            session['parsed_data'],
            session['original_filename']
        )

        return send_file(
            output,
            as_attachment=True,
            download_name=f"parsed_{session['original_filename']}",
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
    except Exception as e:
        flash(f'Error generating document: {str(e)}')
        return redirect(url_for('preview'))

if __name__ == '__main__':
    app.run(debug=True)

