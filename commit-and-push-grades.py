#!/usr/bin/env python3
# Author:  Luke Hindman
# Date: Fri 19 May 2023 01:46:30 PM MDT
# Description: This tool will commit and push GRADE.md files located in student
#    repositories to GitHub. It first connects to Canvas to retrieve the student roster,
#    For each student it then opens the local repo in the specified assignment folder
#    and stages (adds) each GRADE.md to a single commit with is then pushed to GitHub.
#
#    While not strictly necessary, by using the Canvas roster this tool can display info
#    regarding students who do not have a local repo or who do not have a GitHub mapping.
#
#    NOTE: This tool is designed to be used on student repositories that have previously been 
#    cloned from GitHub using the classroom-sync.py tool.
#
#  Usage: commit-and-push-grades.py <assignment> 
#
#   assignment - You can find the list of assignment names on GitHub Classroom

import csv
import sys
import os
import os.path
import json
import subprocess
from subprocess import CalledProcessError

import keyring
from canvasapi import Canvas
import decouple
from decouple import config

# Returns a dictionary containing the classroom
#    configuration information loaded from
#    the specified json formatted config_file.
def load_classroom_config(config_file):
    map_data = {}
    with open(config_file) as json_file:
        map_data = json.load(json_file)
    return map_data

# Loads the canvas URL and security token from the system keystore.
# These can be set using the keyring command as follows:
#    keyring set canvas token
def canvas_connect(api_url):
    # Canvas API key from .env file or CANVAS_TOKEN environment
    #    variable. If this fails, fall back to the OS keyring.
    try: 
        API_KEY = config('CANVAS_TOKEN') 
    except decouple.UndefinedValueError:
        API_KEY = keyring.get_password("canvas","token")

    # Initialize a new Canvas object
    canvas = Canvas(api_url, API_KEY)

    return canvas

# Return a dictionary containing User objects of students
#   enrolled the specified course. The dictionary keys
#   are the canvas user_id numbers.
def canvas_get_students(course):
    student_dict={}
    for user in course.get_users(enrollment_type=['student']):
        student_dict[user.id] = user
    
    return student_dict


# Repositories that that represent multiple assignments will may contain
#   multiple GRADE.md files. The purpose of this function is to begin
#   at the root of a student repository and recusively traverse the entire
#   repo, building a list of GRADE.md files with paths that are relative 
#   to the root.
def get_gradefile_list(student_repo_path):
    gradefile_list=[]

    for dirpath, dirnames, filenames in os.walk(student_repo_path):
        for gradefile in [f for f in filenames if f == "GRADE.md"]:
            full_gradefile_path=os.path.join(dirpath,gradefile)
            relative_gradefile_path=os.path.relpath(full_gradefile_path,start=student_repo_path)
            gradefile_list.append(relative_gradefile_path)

    return gradefile_list

# Return a dictionay containing the github roster mapping
#   with the key valuing being the Canvas username
#   and the value being the github username.
def get_github_roster(roster_file):
    github_roster = {}

    if roster_file == "":
        return github_roster

    roster_list = []
    with open(roster_file,'r',encoding='utf-8-sig') as csvfile:
        reader = csv.DictReader(csvfile, dialect='excel')
        for line in reader:
            roster_list.append(line)

    for entry in roster_list:
        canvas_username = entry['identifier'].split('@')[0].lower()
        github_roster[canvas_username] = entry['github_username']

    return github_roster


def commit_and_push_student_repos(students,github_roster,github_organization,assignment_name,classroom_path,student_filter):
    assignment_path = os.path.join(classroom_path,assignment_name)

    repo_status = {}
    num_students = len(students)
    student_count = 1
    print("Commit and Push Student Repos\n\n")

    for student in students.values():
        canvas_username = student.login_id
        if canvas_username.lower() not in github_roster.keys():
            continue
        github_username = github_roster[canvas_username.lower()]

        if student_filter is None or (student_filter is not None and canvas_username.lower() == student_filter):

            print("%-40s (%s)" % (canvas_username, str(student_count) + "/" + str(num_students)))
            student_count = student_count + 1
            repo_path = os.path.join(assignment_path,canvas_username)

            # Skip users with no mapping to GitHub accounts
            if github_username == "":
                print("- Warning: No GitHub mapping exists for user: " + canvas_username)
                repo_status[canvas_username] = "No GitHub mapping exists"
                continue

            url="git@github.com:" + github_organization + "/" + assignment_name + "-" + github_username + ".git"
            try: 
                if os.path.isdir(os.path.join(repo_path,".git")):
                    # Build a list of GRADE.md files to commit and push
                    gradefile_list = get_gradefile_list(repo_path)
                    for gradefile in gradefile_list:
                        subprocess.run(['git','add',gradefile],cwd=repo_path,capture_output=True,timeout=20,check=True,text=True)
                        print("DEBUG: git add " + gradefile)
                    
                    subprocess.run(['git','commit','-m','Updated grading report'],cwd=repo_path,capture_output=True,timeout=20,check=True,text=True)
                    print("DEBUG: git commit -m 'Updated grading report'")
                    
                    subprocess.run(['git','push'],cwd=repo_path,capture_output=True,timeout=20,check=True,text=True)
                    print("DEBUG: git push")
                    repo_status[canvas_username] = "Detailed grading report pushed to repo: %s" % (url)
                    # print("Detailed grading report pushed to repo: %s" % (url))
                else:
                    print("- Warning: No GitHub submission found for user: " + canvas_username)
                    repo_status[canvas_username] = "No GitHub submission found"
            except CalledProcessError as e:
                print("- Warning: Unable to push repo: " + url)
                print(e.stdout)
                print(e.stderr)
                repo_status[canvas_username] = "Error while pushing grading report to repo"

            except subprocess.TimeoutExpired as e:
                print("- Warning: Unable to push repo (timeout): " + url)
                repo_status[canvas_username] = "Timeout while pushing grading report to repo"

    return repo_status

def main():
    
    # Check the parameters
    if len(sys.argv) < 2:
        print("usage: classroom-sync.py <assignment>")
        sys.exit(1)

    # Parse the command line args
    assignment = sys.argv[1]

    # Load the classroom configuration data
    classroom_config = load_classroom_config("classroom-config.json")

    roster_file = classroom_config['global']['github-roster']
    classroom_path = classroom_config['global']['classroom-path']
    course_id = classroom_config['global']['canvas-course-id']
    api_url = classroom_config['global']['canvas-url']
    github_org = classroom_config['global']['github-org']


    # Connect to the Canvas gradebook
    canvas = canvas_connect(api_url)

    if canvas == None:
        print("Error: Unable to connect to Canvas ")
        sys.exit(1)

    canvas_course = canvas.get_course(course_id)

    if canvas_course == None:
        print("Error: Unable to Canvas course match for: " + course_name)
        sys.exit(1)

    canvas_students = canvas_get_students(canvas_course)

    # Load GitHub Roster and store to dictionary, indexed by Canvas username in lowercase
    github_roster = get_github_roster(roster_file)

    commit_and_push_student_repos(canvas_students,github_roster,github_org,assignment,classroom_path,student_filter=None)


if __name__ == '__main__':
	main()
