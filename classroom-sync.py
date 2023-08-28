#!/usr/bin/env python3
# Author:  Luke Hindman
# Date: Wed 17 May 2023 01:55:54 PM MDT
# Description: Sync tool for GitHub Classroom assignments
#     This tool will retrieve student repositories and rename them
#        to include Canvas username instead of their
#        GitHub username.
#
#     Before using this tool, do the following:
#       1. Set up SSH keybased authentication with GitHub  
#       2. Download the classroom_roster.csv file from GitHub Classroom  
#       3. Update classroom-config.json with the details for your classroom  
#       4. Enable API access for Canvas user account and store token in OS keyring. Details are provided below
#
#  Usage: classroom-sync.py <assignment> [<student_number>]
#
#   assignment - You can find the list of assignment names on GitHub Classroom



import csv
import sys
import os
import json
import subprocess
from subprocess import CalledProcessError
from time import sleep

import keyring
from canvasapi import Canvas
import decouple
from decouple import config

import inspect

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

# Iterates through the list of active courses and returns
#   a course object that matches the specified course_name.
#   if no matching course is found, returns None
def canvas_get_course(canvas,course_name):
    course_dict={}
    for c in canvas.get_courses(state=['available']):

        # Courses that are published, but that are date restricted are still considered "available" :(
        #   This causes access errors and therefore need to be excluded.
        if "access_restricted_by_date" in c.__dict__.keys():
            continue

        course_dict[c.name] = c


    course_match = None
    for c in course_dict.keys():
        if course_name in c:
            course_match = course_dict[c]

    return course_match

# Return a dictionary containing User objects of students
#   enrolled the specified course. The dictionary keys
#   are the canvas user_id numbers.
def canvas_get_students(course):
    student_dict={}
    for user in course.get_users(enrollment_type=['student']):
        student_dict[user.id] = user
    
    return student_dict

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


def clone_student_repos(students,github_roster,github_organization,assignment_name,classroom_path,student_count,student_filter):

    # GitHub repo URLs are in lowercase, so convert specified
    #    assignment name for consistency
    assignment_name = assignment_name.lower()

    # Setup classroom and assignment directory structure 
    if not os.path.isdir(classroom_path):
        os.mkdir(classroom_path)

    assignment_path = os.path.join(classroom_path,assignment_name) 
    if not os.path.isdir(assignment_path):
        os.mkdir(assignment_path)

    repo_status = {}
    students_list=list(students.values())
    students_list.sort(key=lambda x: x.login_id)
    num_students = len(students_list)
    students_list=students_list[student_count-1:]
    print("Cloning Student Repos\n\n")
    for student in students_list:
        canvas_username = student.login_id
        if canvas_username.lower() not in github_roster.keys():
            print("%-40s (%s)" % (canvas_username, str(student_count) + "/" + str(num_students)))
            student_count = student_count + 1
            print("- Warning: User not found on GitHub roster: " + canvas_username)
            repo_status[canvas_username] = "User not found on GitHub roster"
            continue
        github_username = github_roster[canvas_username.lower()]
        
        if student_filter is None or (student_filter is not None and canvas_username.lower() == student_filter):
            print("%-40s (%s)" % (canvas_username, str(student_count) + "/" + str(num_students)))
            student_count = student_count + 1

            # Skip users with no mapping to GitHub accounts
            if github_username == "":
                print("- Warning: No GitHub mapping exists for user: " + canvas_username)
                repo_status[canvas_username] = "No GitHub mapping exists"
                continue

            url="git@github.com:" + github_organization + "/" + assignment_name + "-" + github_username + ".git"
            try: 
                entry_path = os.path.join(assignment_path,canvas_username)
                if os.path.isdir(os.path.join(entry_path,".git")):
                    subprocess.run(['git','pull'],cwd=entry_path,capture_output=True,timeout=20,check=True,text=True)
                    repo_status[canvas_username] = "Repo pulled successfully: %s" % (url)
                else:
                    subprocess.run(['git','clone', url, canvas_username],cwd=assignment_path,capture_output=True,timeout=20,check=True,text=True)
                    repo_status[canvas_username] = "Repo cloned successfully: %s" % (url)
            except CalledProcessError as e:
                    print("- Warning: Unable to clone repo: " + url)
                    repo_status[canvas_username] = "Error while cloning repository"
            except subprocess.TimeoutExpired as e:
                 print("- Warning: Unable to clone repo (timeout): " + url)
                 repo_status[canvas_username] = "Timeout while cloning repository"
            print(repo_status[canvas_username])
    return repo_status


def main():
    
    student_count = 1
    # Check the parameters
    if len(sys.argv) < 2:
        print("usage: classroom-sync.py <assignment> [<student_number>]")
        sys.exit(1)

    # Parse the command line args
    assignment = sys.argv[1]
    if len(sys.argv) == 3:
        try:
            student_count = int(sys.argv[2])
        except:
            print("usage: classroom-sync.py <assignment> [<student_number>]")
            sys.exit(1)

    # Load the classroom configuration data
    classroom_config = load_classroom_config("classroom-config.json")

    roster_file = classroom_config['global']['github-roster']
    classroom_path = classroom_config['global']['classroom-path']
    course_name = classroom_config['global']['canvas-course-name']
    api_url = classroom_config['global']['canvas-url']
    github_org = classroom_config['global']['github-org']

    # Connect to the Canvas gradebook
    canvas = canvas_connect(api_url)

    if canvas == None:
        print("Error: Unable to connect to Canvas ")
        sys.exit(1)

    canvas_course = canvas_get_course(canvas,course_name)

    if canvas_course == None:
        print("Error: Unable to Canvas course match for: " + course_name)
        sys.exit(1)

    canvas_students = canvas_get_students(canvas_course)

    # Load GitHub Roster and store to dictionary, indexed by Canvas username in lowercase
    github_roster = get_github_roster(roster_file)

    clone_student_repos(canvas_students,github_roster,github_org,assignment,classroom_path,student_count,student_filter=None)


if __name__ == '__main__':
	main()