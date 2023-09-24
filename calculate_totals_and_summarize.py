#!/usr/bin/env python3
# Author: Luke Hindman
# Date: Mon Aug 14 09:54:15 MDT 2023
# Description:  Parse the specified GRADE.md file, sum the total scores from each 
#    section and insert a row containing the total value. Display the total scores
#    to stdout as CSV formatted data.
#
# The following shows the sections that will be processed:
#
# Planning                        /6
# Subject Proficiency             /12
# Coding Conventions              /3
# Terminology Identification      /3
# Code Review                     /2
# Reflection                      /4
# -----------------------------------
# Total                         30/30
#
# The line containing the Total is inserted into the GRADE.md in each student repository for the
#     specified assignment.  Once completed, a CSV file is generated in the current directory
#     that contains a summary of all the student scores for the specified assignments.
#
#  Usage: calculate_totals_and_summaryize.py <assignment> 
#
#   assignment - You can find the list of assignment names on GitHub Classroom
#

import re
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

sections=["Planning", "Subject Proficiency", "Coding Conventions", "Terminology Identification", "Code Review", "Reflection"]


# Returns a dictionary where the key is a section from the gradefile
#    and the value is compiled RE that matches the line containing the 
#    score for that section
def generate_section_expressions():
    section_expressions = {}
    for section in sections:
        section_expressions[section]=re.compile("%s[ ]+([0-9]+)/([0-9]+)" % section)
    return section_expressions

# Returns a tuple containing the total score for all the sections withing
#   a single grade file. The format of the tuple is as follows:
#         (points_earned, points_possible)
#
# In the event that a section is missing or does not contain a score, that
#   section is ignored in the calculation. Before returning, this function
#   writes a list of sections that were missing to stderr.
def get_score(gradefile_contents):

    section_expressions = generate_section_expressions()

    points_possible=0
    points_earned=0
    missing_sections = []
    for section in section_expressions.keys():
        found = False
        for line in gradefile_contents:
            section_match = section_expressions[section].search(line)
            if section_match is not None:
                points_earned += int(section_match.group(1))
                points_possible += int(section_match.group(2))
                found = True
                break
        if not found:
            missing_sections.append(section)
    if len(missing_sections) > 0:
        missing_message = "- Warning: The following sections are missing from the gradefile: " + ", ".join(missing_sections)
        print(missing_message,file=sys.stderr)

    return (points_earned, points_possible)


# Modifies the gradefile_contents by inserting the Total score
#   into the gradefile immediately following the section break
#   header. This function is idempotent, in that it can be 
#   executed multiple times, but will only ever insert a single
#   Total score line.  If the line already exists, it is removed (popped)
#   from the list and a new Total score line is inserted after
#   the header.
#
def insert_total_score (gradefile_contents,score):

    total_expression = re.compile("Total[ ]+([0-9]+)/([0-9]+)")
    index = 0
    section_break_index = 0
    total_index = -1
    for line in gradefile_contents:
        if "------------------" in line:
            section_break_index = index
        elif total_expression.search(line) is not None:
            total_index = index
            break
        index += 1

    if total_index > 0:
        gradefile_contents.pop(total_index)
    
    gradefile_contents.insert(section_break_index + 1,"Total                         %d/%d\n" % (score[0],score[1]))

def write_summary_csv(summary_file, summary):

    with open(summary_file, 'w', encoding="utf-8-sig") as csvfile:
        

        # Cleanup the summary output a bit
        scores_by_student = list(summary.keys())
        scores_by_student.sort()

        for student in scores_by_student:
            csvfile.write("%s, %s\n"  % (student.lower(),summary[student]))


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


def calculate_total_and_summarize(students,github_roster,github_organization,assignment_name,classroom_path,student_filter):
    assignment_path = os.path.join(classroom_path,assignment_name)

    repo_status = {}
    num_students = len(students)
    student_count = 1

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


            if os.path.isdir(os.path.join(repo_path,".git")):
                # Build a list of GRADE.md files to commit and push
                gradefile_list = get_gradefile_list(repo_path)
                for gradefile in gradefile_list:
                    gradefile_path = os.path.join(repo_path,gradefile)

                    with open(gradefile_path,mode="r+") as f:
                        gradefile_contents = f.readlines()
                        
                        score = get_score(gradefile_contents)
                        insert_total_score(gradefile_contents,score)

                        # repo_status[canvas_username] = "%d/%d" % (score[0],score[1])
                        repo_status[canvas_username] = "%d" % (score[0])

                        f.seek(0)
                        f.writelines(gradefile_contents)
                        f.close()  

                
                # print("Detailed grading report pushed to repo: %s" % (url))
            else:
                print("- Warning: No GitHub submission found for user: " + canvas_username)
                repo_status[canvas_username] = "No GitHub submission found"


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

    print("Calculating Totals")
    summary = calculate_total_and_summarize(canvas_students,github_roster,github_org,assignment,classroom_path,student_filter=None)
   
        
    summary_file = "%s-summary.csv" % assignment
    print("\n\nWriting Summary CSV: %s" % summary_file)
    write_summary_csv(summary_file,summary)


if __name__ == '__main__':
	main()

