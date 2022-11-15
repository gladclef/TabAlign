import sublime
import sublime_plugin
from datetime import datetime
from inspect import currentframe
import math

def get_linenumber():
    cf = currentframe()
    return cf.f_back.f_lineno

class Timer():
	def __init__(self, tick_secs):
		self.dt_start = datetime.now()
		self.tick_secs = tick_secs

	def elapsed(self):
		dt_curr = datetime.now()
		dt_diff = dt_curr - self.dt_start
		return dt_diff.total_seconds()

	def ticks(self):
		return math.floor(self.elapsed() / self.tick_secs)

	def has_ticked(self):
		return self.ticks() > 0

class LineObj():
	def __init__(self, begin, end, text):
		self.begin = begin
		self.end = end
		self.text = text
		self.search_start = 0
		self.last_find = 0

	def find_next(self, alignstr, alignfirst):
		pos = self.text.find(alignstr, self.search_start)
		if (pos == -1) or (pos >= len(self.text)):
			self.search_start = len(self.text)
			return -1
		self.search_start = pos+len(alignstr)
		self.last_find = pos
		
		# don't keep searching if align first
		# allows us to align tables like this:
		#     this|is||a|table
		#     it||has|some|values
		if alignfirst:
			return pos	

		# keep searching, choose the last value in a row
		# allows aligning by spaces in situations like:
		#     list1a, list1b, list1c
		#     list2a,  list2b,  list2c
		#     list20a, list20b, list20c
		asl = len(alignstr)
		while (pos < len(self.text)) and (self.text.find(alignstr, pos+asl) == pos+asl):
			pos += asl

		# remember where to start searching next time
		self.search_start = pos + len(alignstr)
		self.last_find = pos

		ret = pos if not alignfirst else firstpos
		return ret

	def insert(self, pos, val):
		pos = min(max(pos, 0), len(self.text))
		if pos <= self.search_start:
			self.search_start += len(val)
		if pos <= self.last_find:
			self.last_find += len(val)
		if pos == 0:
			self.text = val + self.text
		elif pos == len(self.text):
			self.text += val
		else:
			self.text = self.text[:pos] + val + self.text[pos:]

	def __str__(self):
		return str([self.begin, self.end, self.text])

	def __repr__(self):
		return self.__str__()

class TabAlign(sublime_plugin.TextCommand):
	def my_run(self, edit, alignfirst=False):
		# determine the mode
		cursors = list(self.view.sel())
		timer = Timer(10)
		if len(cursors) == 1:
			self.align_by_selected_str(cursors[0], timer, edit, alignfirst)
		else:
			self.align_by_cursors(timer, edit)

	def align_by_cursors(self, timer, edit):
		cursors = list(self.view.sel())
		waiting_cursors = [sublime.Region(c.begin(), c.end()) for c in cursors]
		for cursor in cursors:
			if cursor.size() > 0:
				self.view.window().status_message("Error: you must have either a selection, a single cursor, or multiple cursors")
				return None, None

		while len(waiting_cursors) > 0:
			if timer.has_ticked():
				self.view.window().status_message("Programmer error: timeout on line " + str(get_linenumber()))
				return

			# get the cursor locations (in reverse order)
			active_cursors, waiting_cursors, maxpos = self.get_active_cursors(waiting_cursors, timer)
			if active_cursors == None:
				return
				
			# tab align!
			for selreg in active_cursors:
				loc = selreg['loc']
				pos = selreg['pos']
				insertion_len = maxpos - pos
				if insertion_len > 0:
					spaces = " "*insertion_len
					self.view.insert(edit, loc, spaces)

				# also increment cursor positions
				for reg_idx in range(len(waiting_cursors)):
					reg = waiting_cursors[reg_idx]
					if reg.begin() >= loc:
						waiting_cursors[reg_idx] = sublime.Region(reg.begin()+insertion_len, reg.begin()+insertion_len)

	def get_active_cursors(self, search_cursors, timer):
		tabsize = self.view.settings().get('tab_size')

		# get each cursor's properties
		rich_cursors = []
		for reg in search_cursors:
			loc = reg.begin()
			row, col = self.view.rowcol(loc)
			line = self.view.line(loc)
			linestr = self.view.substr(line)[:col]
			pos = col + linestr.count("\t") * (tabsize-1)
			rich_cursors.append({'reg': reg, 'loc': loc, 'line': line, 'row': row, 'col': col, 'pos': pos})
			if timer.has_ticked():
				self.view.window().status_message("Programmer error: timeout on line " + str(get_linenumber()))
				return None, None

		# sort by position, in reverse order
		rich_cursors = list(sorted(rich_cursors, key=lambda x: x['loc']))

		# get the first selection on each line
		prevline = -1
		first_richcursors = []
		other_cursors = []
		for rich_cursor in rich_cursors:
			line = rich_cursor['line'].begin()
			if line != prevline:
				first_richcursors.append(rich_cursor)
			else:
				other_cursors.append(rich_cursor['reg'])
			prevline = line

		# sort by position, in reverse order
		first_richcursors = list(sorted(first_richcursors, key=lambda x: x['loc'], reverse=True))

		# find the max position among selections
		positions = [rich_cursor['pos'] for rich_cursor in first_richcursors]
		maxpos = max(positions)

		return first_richcursors, other_cursors, maxpos

	def align_by_selected_str(self, cursor, timer, edit, alignfirst=False):
		# find the selected character in every subsequent line and align by it
		# For example, if either ' ' or '|' were selected in the first line of:
		#     this | is    | a | table
		#     it | has | some    | values
		# Then the result would be:
		#     this | is    | a       | table
		#     it   | has   | some    | values
		cursorline = self.view.line(cursor.begin())
		if self.view.line(cursor.end()).begin() != cursorline.begin():
			self.view.window().status_message("Error: selection can't span more than one line")
			return
		if cursor.size() > 0:
			alignstr = self.view.substr(cursor)
		else:
			alignstr = self.view.substr(sublime.Region(cursor.begin(), cursor.begin()+1))
		if alignstr == "":
			self.view.window().status_message("Programmer error: empty align string")

		# find the lines with the desired alignstr
		lines = []
		line_start = cursorline.begin()
		line = self.get_line(line_start)
		while line != None and alignstr in line.text:
			lines.append(line)
			# next line
			line_start = line.end + 1
			line = self.get_line(line_start)
			if timer.has_ticked():
				self.view.window().status_message("Programmer error: timeout on line " + str(get_linenumber()))
				return
		origreg = sublime.Region(lines[0].begin, lines[-1].end)
		
		# align by the align string, one "column" at a time
		while True:
			if timer.has_ticked():
				self.view.window().status_message("Programmer error: timeout on line " + str(get_linenumber()))
				return

			# find the align position
			align_positions = [line.find_next(alignstr, alignfirst) for line in lines]
			maxpos = max(align_positions)
			if maxpos < 0:
				break

			# align each line
			for line in lines:
				linepos = line.last_find
				if linepos < 0:
					continue
				if linepos < maxpos:
					line.insert(linepos, " "*(maxpos-linepos))

		# create a new string for all the lines and update the view
		linestrs = []
		for line in lines:
			linestrs.append(line.text)
		newstrval = '\n'.join(linestrs)
		self.view.replace(edit, origreg, newstrval)

	def get_line(self, loc):
		if self.view.size() <= loc:
			return None
		line = self.view.line(loc)
		text = self.view.substr(line)
		return LineObj(line.begin(), line.end(), text)

class TabAlignLastCommand(TabAlign):
	# Ctrl+Alt+Tab on one of the bars to go from this:
	#     this|is||a|table
	#     it||has|some|values
	# To this:
	#     this|is|   |a   |table
	#     it  |  |has|some|values
	def run(self, edit):
		self.my_run(edit, alignfirst=False)

class TabAlignFirstCommand(TabAlign):
	# Ctrl+Tab on one of the spaces to go from this:
	#     this | is    | a | table
	#     it | has | some    | values
	# To this:
	#     this | is    | a       | table
	#     it   | has   | some    | values
	def run(self, edit):
		self.my_run(edit, alignfirst=True)