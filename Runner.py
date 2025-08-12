import subprocess
import sys
import argparse

def run_script(script_name, args):
    """Run a Python script with the given arguments."""
    command = [sys.executable, script_name] + args
    try:
        result = subprocess.run(command, check=True, text=True)
        print(f"Successfully ran {script_name}")
    except subprocess.CalledProcessError as e:
        print(f"Error running {script_name}: {e}")
        sys.exit(1)

def main():
    # Parse arguments for the runner
    parser = argparse.ArgumentParser(description="Run Importer, ColumnUpdater, and DependencyUpdater scripts.")
    parser.add_argument("--dry-run", action="store_true", help="Run all scripts in dry-run mode.")
    args = parser.parse_args()

    # Define the CSV file and project key
    csv_file = "FS_EV_Gantt_Chart.csv"
    project_key = "FS_EV"

    # Determine if dry-run mode is enabled
    dry_run_flag = ["--dry-run"] if args.dry_run else []

    # Run Importer.py
    print("Running Importer.py...")
    run_script("Importer.py", [
        "--csv", csv_file,
        "--project-key", project_key
    ] + dry_run_flag)

    # Run ColumnUpdater.py
    print("Running ColumnUpdater.py...")
    run_script("ColumnUpdater.py", [
        "--csv", csv_file,
        "--project-key", project_key,
        "--startdate-field", "customfield_12345"  # Replace with your actual custom field ID if needed
    ] + dry_run_flag)

    # Run DependencyUpdater.py
    print("Running DependencyUpdater.py...")
    run_script("DependencyUpdater.py", [
        "--csv", csv_file,
        "--project-key", project_key
    ] + dry_run_flag)

    print("All scripts executed successfully.")

if __name__ == "__main__":
    main()