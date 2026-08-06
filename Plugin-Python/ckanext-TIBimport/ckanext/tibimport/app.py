from rdfizer import semantify
import os
from flask import Flask, request, jsonify, send_from_directory
from jsonpath_ng import jsonpath, parse
import json
import re

app = Flask(__name__)

def execute_fuction(value, func_dic):
	func_list = {"toLower":"",
				 "tagsToList":"",
				 "edxTagsToList":"",
				 "osfApiStoragetoHtmlStorage":"",
				 "normalizeName":"",
				 "doiLink":""}
	if func_dic["function"] in func_list:
		if "toLower" == func_dic["function"]:
			value = value.lower()
		elif "tagsToList" == func_dic["function"]:
			return_value = []
			for i in value:
				return_value.append({"name": i})
			value = return_value
		elif "edxTagsToList" == func_dic["function"]:
			return_value = []
			for i in value:
				return_value.append({"name": i["name"]})
			value = return_value
		elif "osfApiStoragetoHtmlStorage" == func_dic["function"]:
			pattern = r"^https://api\.osf\.io/v2/nodes/([a-zA-Z0-9]+)/files/.*$"
			replacement = r"https://osf.io/\1/files"
			formatted_url = re.sub(pattern, replacement, value)
			if formatted_url == value:
				return "Error: URL did not match expected API format."
			value = formatted_url
		elif "normalizeName" == func_dic["function"]:
			if "," in value:
				temp_value_list = value.split(",")
				value = temp_value_list[1] + " " + temp_value_list[0]
		elif "doiLink" == func_dic["function"]:
			value = "https://doi.org/" + value
	return value

@app.route("/")
def home():
    return jsonify({"message": "Flask File API is running"})

@app.route("/integration", methods=["POST"])
def metadata_parser():
	ldm_list_datasets = []
	mapping = request.form.get("mapping")
	output = request.form.get("output")

	with open(mapping, "r") as file:
		property_list = json.load(file)
		with open(property_list["source"], "r") as source_file:
			metadata = json.load(source_file)
			jsonpath_expr = parse(property_list["iterator"])
			datasets = [match.value for match in jsonpath_expr.find(metadata)]
			for row in datasets:
				ldm_dataset = {}
				for dic_property in property_list["properties"]:
					if dic_property["ldm_property"] == "authors":
						jsonpath_expr = parse(dic_property["source_property"])
						matches = [match.value for match in jsonpath_expr.find(row)]
						if len(matches) > 1:
							ldm_dataset["extra_authors"] = []
						ldm_dataset["author"] = ""
						if matches != []:
							for value in matches:
								if value == matches[0]:
									if "transformation_function" in dic_property:
										new_value = execute_fuction(value,dic_property["transformation_function"])
										ldm_dataset["author"] = new_value
									else:
										ldm_dataset["author"] = value
								else:
									if "transformation_function" in dic_property:
										new_value = execute_fuction(value,dic_property["transformation_function"])
										ldm_dataset["extra_authors"].append(new_value)
									else:
										ldm_dataset["extra_authors"].append(value)
					elif dic_property["ldm_property"] == "keywords":
						jsonpath_expr = parse(dic_property["source_property"])
						matches = [match.value for match in jsonpath_expr.find(row)]
						if len(matches) > 1:
							ldm_dataset["keywords"] = []
							for value in matches:
								if "transformation_function" in dic_property:
									new_value = execute_fuction(value,dic_property["transformation_function"])
									ldm_dataset["keywords"].append(new_value)
								else:
									ldm_dataset["keywords"].append(value)
					else:
						jsonpath_expr = parse(dic_property["source_property"])
						matches = [match.value for match in jsonpath_expr.find(row)]
						if matches[0] != None:
							if "transformation_function" in dic_property:
								value = execute_fuction(matches[0],dic_property["transformation_function"])
								ldm_dataset[dic_property["ldm_property"]] = value
							else:
								ldm_dataset[dic_property["ldm_property"]] = matches[0]
				if "resources" in property_list:
					ldm_dataset["resources"] = []
					if property_list["resources"]["iterator"] == "":
						resources_list = [row]
					else:
						jsonpath_expr = parse(property_list["resources"]["iterator"])
						resources_list = [match.value for match in jsonpath_expr.find(row)]
					for resource in resources_list:
						ldm_resource = {}
						for resource_property in property_list["resources"]["properties"]:
							jsonpath_expr = parse(resource_property["source_resource_property"])
							matches = [match.value for match in jsonpath_expr.find(resource)]
							if matches[0] != None:
								if "transformation_function" in resource_property:
									value = execute_fuction(matches[0],resource_property["transformation_function"])
									ldm_resource[resource_property["ldm_resource_property"]] = value
								else:
									ldm_resource[resource_property["ldm_resource_property"]] = matches[0]
						ldm_dataset["resources"].append(ldm_resource)
				if ldm_dataset["author"] != "" and "title" in ldm_dataset:
					print(ldm_dataset["title"])
					ldm_list_datasets.append(ldm_dataset)

			with open(output + "/metadata.json", "w") as file:
				json.dump(ldm_list_datasets, file, indent=4)

			return "Metadata file generated succesfully.\n"

if __name__ == "__main__":
	app.run(host="0.0.0.0", port=8001, debug=True)