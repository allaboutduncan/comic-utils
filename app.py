from flask import Flask, render_template, request, Response
import subprocess
import os

app = Flask(__name__)

@app.route('/stream/<script_type>')
def stream_logs(script_type):
    file_path = request.args.get('file_path')  # Get file_path for single_file script
    directory = request.args.get('directory')  # Get directory for rebuild/rename script

    # Check if the correct parameter is passed for 'single_file' script
    if script_type in ['single_file', 'crop', 'remove']:
        print(script_type)
        if not file_path:
            return Response("Missing file_path for single_file/crop/remove script.", status=400)
        elif not os.path.isfile(file_path):
            return Response("Invalid file_path.", status=400)

        script_file = f"{script_type}.py"

        def generate_logs():
            process = subprocess.Popen(
                ['python', script_file, file_path if script_type in ['single_file', 'crop', 'remove'] else directory],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            # Capture both stdout and stderr
            for line in process.stdout:
                yield f"data: {line}\n\n"  # Format required by SSE
            for line in process.stderr:
                yield f"data: ERROR: {line}\n\n"
            process.wait()
            yield f"data: Process completed with return code {process.returncode}.\n\n"

        return Response(generate_logs(), content_type='text/event-stream')

    elif script_type in ['rebuild', 'rename']:
        if not directory or not os.path.isdir(directory):
            return Response("Invalid or missing directory path.", status=400)

        script_file = f"{script_type}.py"

        def generate_logs():
            process = subprocess.Popen(
                ['python', script_file, directory],  # Use directory for rebuild/rename
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            for line in process.stdout:
                yield f"data: {line}\n\n"  # Format required by SSE
            for line in process.stderr:
                yield f"data: ERROR: {line}\n\n"
            process.wait()
            yield f"data: Process completed with return code {process.returncode}.\n\n"

        return Response(generate_logs(), content_type='text/event-stream')

    return Response("Invalid script type.", status=400)


# Main route to render the homepage
@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5577)
